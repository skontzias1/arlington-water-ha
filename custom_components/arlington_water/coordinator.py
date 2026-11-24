"""Cached, rate-limited updates that cannot hold up Home Assistant."""

import logging
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .client import CannotConnect, Client, InvalidAuth
from .const import CONF_BLOCKED, CONF_METERS, DOMAIN
from .portal import BadPage, Bill, Summary, daily_rows, merge

_LOGGER = logging.getLogger(__name__)

PORTAL_EVERY = timedelta(hours=24)
# Also the floor across restarts and failures.
PORTAL_RETRY = timedelta(hours=6)
MAX_FAILURES = 5  # then back off to weekly
PORTAL_BACKOFF = timedelta(days=7)

type ArlingtonConfigEntry = ConfigEntry[ArlingtonCoordinator]


@dataclass
class State:
    """Everything the sensors use."""

    bills: list[Bill] = field(default_factory=list)
    summary: Summary | None = None
    last_success: datetime | None = None
    meters: dict[str, float] = field(default_factory=dict)


def _pack(obj: Any) -> dict[str, Any]:
    return {
        f.name: v.isoformat() if isinstance(v := getattr(obj, f.name), date) else v
        for f in fields(obj)
    }


def _unpack(cls: type, raw: dict[str, Any], dates: set[str]) -> Any:
    return cls(
        **{k: date.fromisoformat(v) if k in dates and v else v for k, v in raw.items()}
    )


class ArlingtonCoordinator(DataUpdateCoordinator[State]):
    """Poll the portal daily; refresh local gallons hourly."""

    config_entry: ArlingtonConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ArlingtonConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="Arlington Water",
            update_interval=timedelta(hours=1),
        )
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}", private=True
        )
        self.data = State()
        self.failures = 0
        self.last_error: str | None = None
        self._next_portal: datetime | None = None
        self._imported: list[Bill] | None = None

    async def async_load_cache(self) -> None:
        """Load saved values. A damaged cache is ignored, never fatal."""
        try:
            raw = await self._store.async_load() or {}
            self.data = State(
                [
                    _unpack(Bill, b, {"start", "end", "bill_date"})
                    for b in raw.get("bills", [])
                ],
                _unpack(Summary, raw["summary"], {"due_date", "last_payment_date"})
                if raw.get("summary")
                else None,
                dt_util.parse_datetime(raw.get("last_success") or ""),
            )
            self.failures = int(raw.get("failures", 0))
            self.last_error = raw.get("last_error")
            self._next_portal = dt_util.parse_datetime(raw.get("next_portal") or "")
            # A 0.1 cache has no account type: refresh once. The next save adds the key.
            if raw.get("summary") and "account_type" not in raw["summary"]:
                self._next_portal = None
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Ignoring unreadable Arlington Water cache")
            self.data = State()
            # Still no immediate sign-in, so a crash loop cannot hammer the site.
            self._next_portal = dt_util.utcnow() + timedelta(minutes=15)

    async def _save(self) -> None:
        await self._store.async_save(
            {
                "bills": [_pack(b) for b in self.data.bills],
                "summary": _pack(self.data.summary) if self.data.summary else None,
                "last_success": self.data.last_success.isoformat()
                if self.data.last_success
                else None,
                "failures": self.failures,
                "last_error": self.last_error,
                "next_portal": self._next_portal.isoformat()
                if self._next_portal
                else None,
            }
        )

    async def _async_update_data(self) -> State:
        try:
            await self._local_gallons()
            if self._imported is None and self.data.bills:
                self._import(self.data.bills)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Local statistics unavailable: %s", type(err).__name__)

        if self.config_entry.data.get(CONF_BLOCKED):
            raise ConfigEntryAuthFailed(
                "Sign-in was rejected; waiting for a new password"
            )
        now = dt_util.utcnow()
        if self._next_portal and now < self._next_portal:
            if self.failures:
                raise UpdateFailed(f"Arlington Water: {self.last_error}")
            return self.data

        # Saved before signing in: a crash or restart cannot cause a login loop.
        self._next_portal = now + PORTAL_RETRY
        await self._save()
        entry = self.config_entry
        try:
            client = Client(
                self.hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
            )
            try:
                summary, fetched = await client.fetch()
            finally:
                client.close()
            if self.data.bills and not fetched:
                raise BadPage("bills disappeared")
        except InvalidAuth as err:
            self.last_error = "sign-in rejected"
            await self._save()
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_BLOCKED: True}
            )
            raise ConfigEntryAuthFailed("Arlington Water sign-in rejected") from err
        except Exception as err:  # noqa: BLE001 - nothing may escape into HA
            self.failures += 1
            self.last_error = (
                str(err)
                if isinstance(err, (CannotConnect, BadPage))
                else type(err).__name__
            )
            self._next_portal = now + (
                PORTAL_BACKOFF if self.failures >= MAX_FAILURES else PORTAL_RETRY
            )
            await self._save()
            raise UpdateFailed(f"Arlington Water: {self.last_error}") from err

        self.failures = 0
        self.last_error = None
        self._next_portal = now + PORTAL_EVERY
        self.data.summary = summary
        self.data.bills = merge(self.data.bills, fetched)
        self.data.last_success = now
        await self._save()
        try:
            if self.data.bills != self._imported:
                self._import(self.data.bills)
            await self._local_gallons()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Bill history not imported: %s", type(err).__name__)
        return self.data

    async def _local_gallons(self) -> None:
        """Gallons the chosen sensors measured since the county's last read."""
        meters = set(self.config_entry.options.get(CONF_METERS, []))
        if not self.data.bills or not meters:
            return
        start = dt_util.start_of_local_day(self.data.bills[-1].end)
        stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            None,
            meters,
            "day",
            {"volume": UnitOfVolume.GALLONS},
            {"change"},
        )

        totals = {}
        for meter in sorted(meters):
            rows = [r["change"] for r in stats.get(meter, []) if r.get("change")]
            if rows:
                totals[meter] = round(max(0.0, sum(rows)), 1)
        self.data.meters = totals

    def _import(self, bills: list[Bill]) -> None:
        """Write daily rows; existing rows at the same time are overwritten."""
        rows = daily_rows(bills, dt_util.get_default_time_zone())
        for key, name, unit_class, unit, data in (
            (
                "consumption",
                "Arlington County water (billed)",
                VolumeConverter.UNIT_CLASS,
                UnitOfVolume.GALLONS,
                [StatisticData(start=t, state=g, sum=gs) for t, g, gs, _, _ in rows],
            ),
            (
                "cost",
                "Arlington County water, sewer and refuse (billed)",
                None,
                None,
                [StatisticData(start=t, state=c, sum=cs) for t, _, _, c, cs in rows],
            ),
        ):
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE,
                    has_sum=True,
                    name=name,
                    source=DOMAIN,
                    statistic_id=f"{DOMAIN}:{key}",
                    unit_class=unit_class,
                    unit_of_measurement=unit,
                ),
                data,
            )
        self._imported = list(bills)

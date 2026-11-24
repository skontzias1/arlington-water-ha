"""Arlington Water sensors."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import rates
from .const import CONF_REFUSE, CONF_WINTER_TG, DOMAIN
from .coordinator import ArlingtonConfigEntry, ArlingtonCoordinator, State

type Value = float | int | date | datetime | None


def _bill(s: State):
    return s.bills[-1] if s.bills else None


def _gallons(s: State) -> float | None:
    return round(sum(s.meters.values()), 1) if s.meters else None


def _priceable(s: State) -> bool:
    """Only residential accounts use these rates."""
    return bool(s.meters and s.summary and s.summary.account_type == "Residential")


@dataclass(frozen=True, kw_only=True)
class Description(SensorEntityDescription):
    """Describe a sensor."""

    value: Callable[[State], Value] | None = None
    always: bool = False


def _money(key: str, name: str, value: Callable[[State], Value]) -> Description:
    return Description(
        key=key,
        name=name,
        value=value,
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
    )


def _date(key: str, name: str, value: Callable[[State], Value]) -> Description:
    return Description(
        key=key, name=name, value=value, device_class=SensorDeviceClass.DATE
    )


def _gal(key: str, name: str, value: Callable[[State], Value]) -> Description:
    return Description(
        key=key,
        name=name,
        value=value,
        native_unit_of_measurement=UnitOfVolume.GALLONS,
        icon="mdi:water",
        suggested_display_precision=0,
    )


SENSORS = (
    _money("balance", "Balance", lambda s: s.summary and s.summary.balance),
    _money("past_due", "Past due", lambda s: s.summary and s.summary.past_due),
    _date("due_date", "Bill due", lambda s: s.summary and s.summary.due_date),
    _money(
        "last_payment", "Last payment", lambda s: s.summary and s.summary.last_payment
    ),
    _date(
        "last_payment_date",
        "Last payment date",
        lambda s: s.summary and s.summary.last_payment_date,
    ),
    _money("last_bill", "Last bill", lambda s: (b := _bill(s)) and b.amount),
    _date(
        "last_bill_date", "Last bill date", lambda s: (b := _bill(s)) and b.bill_date
    ),
    _gal(
        "last_bill_gallons",
        "Last bill gallons",
        lambda s: (b := _bill(s)) and b.gallons,
    ),
    _date("last_read", "Last meter read", lambda s: (b := _bill(s)) and b.end),
    _gal(
        "meter_reading",
        "Meter reading",
        lambda s: (b := _bill(s)) and b.reading_tg * 1000,
    ),
    Description(
        key="last_bill_days",
        name="Last bill days",
        native_unit_of_measurement="d",
        icon="mdi:calendar-range",
        value=lambda s: (b := _bill(s)) and (b.end - b.start).days,
    ),
    _gal("quarter_gallons", "Gallons since last read", _gallons),
    Description(
        key="quarter_estimate",
        name="Estimated bill so far",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
    ),
    Description(
        key="water_rate",
        name="Water rate",
        native_unit_of_measurement="USD/gal",
        icon="mdi:cash",
        suggested_display_precision=5,
    ),
    Description(
        key="last_success",
        name="Last successful update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.last_success,
        always=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ArlingtonConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    async_add_entities(ArlingtonSensor(entry.runtime_data, d) for d in SENSORS)


class ArlingtonSensor(CoordinatorEntity[ArlingtonCoordinator], SensorEntity):
    """One value. Keeps its last value while the portal is down."""

    entity_description: Description
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ArlingtonCoordinator, description: Description
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry_id = coordinator.config_entry.entry_id
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Arlington Water",
            manufacturer="Arlington County",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        return self.entity_description.always or self.native_value is not None

    @property
    def native_value(self) -> Value:
        key, data = self.entity_description.key, self.coordinator.data
        if key in ("quarter_estimate", "water_rate"):
            if not _priceable(data):
                return None
            options = self.coordinator.config_entry.options
            now = rates.current(dt_util.now().date())
            gallons, winter = _gallons(data), options.get(CONF_WINTER_TG)
            if key == "water_rate":
                return rates.next_gallon(now, gallons, winter)
            return rates.estimate(now, gallons, winter, options.get(CONF_REFUSE, True))
        return self.entity_description.value(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        key, c = self.entity_description.key, self.coordinator
        if key == "last_success":
            return {"consecutive_failures": c.failures, "last_error": c.last_error}
        if key == "water_rate":
            now = dt_util.now().date()
            through = rates.current(now).through
            return {
                "rates_through": through.isoformat(),
                "rates_expired": now > through,
            }
        if key == "quarter_gallons":
            return {"meters": c.data.meters}
        return None

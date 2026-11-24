"""Arlington Water: read-only county water bills."""

from datetime import datetime

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .coordinator import ArlingtonConfigEntry, ArlingtonCoordinator

PLATFORMS = [Platform.SENSOR]
START_DELAY = 60


async def async_setup_entry(hass: HomeAssistant, entry: ArlingtonConfigEntry) -> bool:
    """Set up from the local cache only; the network waits until HA is running."""
    coordinator = ArlingtonCoordinator(hass, entry)
    await coordinator.async_load_cache()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def started(_: HomeAssistant) -> None:
        @callback
        def refresh(_now: datetime) -> None:
            entry.async_create_background_task(
                hass, coordinator.async_refresh(), "arlington_water refresh"
            )

        entry.async_on_unload(async_call_later(hass, START_DELAY, refresh))

    entry.async_on_unload(async_at_started(hass, started))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ArlingtonConfigEntry) -> bool:
    """Unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ArlingtonConfigEntry) -> None:
    """Remove the cache."""
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}", private=True).async_remove()

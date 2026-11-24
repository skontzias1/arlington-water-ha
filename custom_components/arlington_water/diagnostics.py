"""Diagnostics without login, account, meter or bill amounts."""

from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_METERS
from .coordinator import ArlingtonConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ArlingtonConfigEntry
) -> dict[str, Any]:
    """Return diagnostics."""
    c = entry.runtime_data
    return {
        "last_update_success": c.last_update_success,
        "consecutive_failures": c.failures,
        "last_error": c.last_error,
        "bills": len(c.data.bills),
        "has_summary": c.data.summary is not None,
        "configured_meters": len(entry.options.get(CONF_METERS, [])),
    }

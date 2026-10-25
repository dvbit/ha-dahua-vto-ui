"""Diagnostics with sensitive data redacted (SPEC §8)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import DahuaConfigEntry
from .const import CONF_SERIAL

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_SERIAL, "serial_number", "serialNumber"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DahuaConfigEntry
) -> dict[str, Any]:
    """Entry, system info, connection state and recent events."""
    device = entry.runtime_data
    return async_redact_data(
        {
            "entry": {"data": dict(entry.data), "options": dict(entry.options)},
            "kind": device.kind,
            "system_info": device.system_info,
            "connected": device.connected,
            "connected_since": device.connected_since,
            "recent_events": list(device.events),
        },
        TO_REDACT,
    )

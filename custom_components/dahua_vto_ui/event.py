"""Event entities: doorbell and access control (SPEC §4.2, §12.2-12.3, VTO only).

The interpretation of the raw device events lives in history.py (shared with
the access log); these entities only receive the derived signals.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DahuaConfigEntry
from .const import ACCESS_EVENT_TYPES, DOORBELL_EVENT_TYPES
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity

# Access entry keys exposed as event attributes (SPEC §4.2, §12.2-12.3).
ACCESS_ATTRS = ("method", "method_name", "user_id", "card_no", "fingerprint_id", "person")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add doorbell and access events."""
    device = entry.runtime_data
    async_add_entities([DahuaDoorbellEvent(device), DahuaAccessEvent(device)])


class DahuaDoorbellEvent(DahuaVtoEntity, EventEntity):
    """Doorbell events: ring (once per call), answered, missed, voice message."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = DOORBELL_EVENT_TYPES

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the doorbell event."""
        super().__init__(device, "event", "doorbell")

    async def async_added_to_hass(self) -> None:
        """Subscribe to interpreted doorbell events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.device.async_subscribe(self.device.signal_doorbell, self._handle))

    @callback
    def _handle(self, event_type: str, attrs: dict[str, Any]) -> None:
        self._trigger_event(event_type, attrs)
        self.async_write_ha_state()


class DahuaAccessEvent(DahuaVtoEntity, EventEntity):
    """Access events with method, fingerprint and mapped person."""

    _attr_event_types = ACCESS_EVENT_TYPES

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the access event."""
        super().__init__(device, "event", "access")

    async def async_added_to_hass(self) -> None:
        """Subscribe to interpreted access events."""
        await super().async_added_to_hass()
        self.async_on_remove(self.device.async_subscribe(self.device.signal_access, self._handle))

    @callback
    def _handle(self, entry: dict[str, Any]) -> None:
        self._trigger_event(entry["result"], {k: entry.get(k) for k in ACCESS_ATTRS})
        self.async_write_ha_state()

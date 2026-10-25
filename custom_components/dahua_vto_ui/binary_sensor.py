"""Door status binary sensor from ``DoorStatus`` events (SPEC §4.2, VTO only).

Replaces the "Door status sensor" template example of the original README.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import DahuaConfigEntry
from .const import CODE_DOOR_STATUS
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the door status sensor."""
    async_add_entities([DahuaDoorStatus(entry.runtime_data)])


class DahuaDoorStatus(DahuaVtoEntity, BinarySensorEntity, RestoreEntity):
    """``on`` = open; unknown until the first event, then restored (SPEC §4.2)."""

    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the door status sensor."""
        super().__init__(device, "binary_sensor", "door_status")
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        """Restore the last state and listen for ``DoorStatus``."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None and last.state not in (
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
        ):
            self._attr_is_on = last.state == STATE_ON
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_event, self._handle_event)
        )

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("Code") != CODE_DOOR_STATUS:
            return
        status = str((event.get("Data") or {}).get("Status"))
        self._attr_is_on = status == "Open"
        self.async_write_ha_state()

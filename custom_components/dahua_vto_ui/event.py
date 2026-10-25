"""Event entities: doorbell and access control (SPEC §4.2, VTO only)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DahuaConfigEntry
from .const import (
    ACCESS_AUTHORIZED,
    ACCESS_DENIED,
    ACCESS_EVENT_TYPES,
    BKL_ANSWERED,
    BKL_KNOWN,
    BKL_MISSED,
    BKL_RING,
    BKL_VOICE_MESSAGE,
    CODE_ACCESS_CONTROL,
    CODE_BACK_KEY_LIGHT,
    DOORBELL_ANSWERED,
    DOORBELL_EVENT_TYPES,
    DOORBELL_MISSED,
    DOORBELL_RING,
    DOORBELL_VOICE_MESSAGE,
)
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity

_LOGGER = logging.getLogger(__name__)

# BackKeyLight state → doorbell event type (README table, SPEC §4.2).
# Ring states are handled apart so a call fires ``ring`` only once.
BKL_EVENT_MAP: dict[int, str] = {
    BKL_VOICE_MESSAGE: DOORBELL_VOICE_MESSAGE,
    BKL_ANSWERED: DOORBELL_ANSWERED,
    BKL_MISSED: DOORBELL_MISSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add doorbell and access events."""
    device = entry.runtime_data
    async_add_entities([DahuaDoorbellEvent(device), DahuaAccessEvent(device)])


class _DahuaEventBase(DahuaVtoEntity, EventEntity):
    """Event entity listening to device notifications."""

    async def async_added_to_hass(self) -> None:
        """Subscribe to device events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_event, self._handle_event)
        )

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        raise NotImplementedError


class DahuaDoorbellEvent(_DahuaEventBase):
    """Doorbell events from ``BackKeyLight`` (SPEC §4.2)."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types = DOORBELL_EVENT_TYPES

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the doorbell event."""
        super().__init__(device, "event", "doorbell")
        self._last_state: int | None = None

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("Code") != CODE_BACK_KEY_LIGHT:
            return
        data = event.get("Data") or {}
        try:
            state = int(data.get("State"))
        except (TypeError, ValueError):
            return
        if state not in BKL_KNOWN:
            _LOGGER.warning("%s: unknown BackKeyLight state %s", self.device.name, state)
        previous, self._last_state = self._last_state, state
        attrs = {"state": state, "utc": data.get("UTC")}
        if state in BKL_RING:
            if previous in BKL_RING:
                return  # same call still ringing
            event_type = DOORBELL_RING
        elif (event_type := BKL_EVENT_MAP.get(state)) is None:
            return
        _LOGGER.debug("%s doorbell %s", self.device.name, event_type)
        self._trigger_event(event_type, attrs)
        self.async_write_ha_state()


class DahuaAccessEvent(_DahuaEventBase):
    """Access control events (SPEC §4.2)."""

    _attr_event_types = ACCESS_EVENT_TYPES

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the access event."""
        super().__init__(device, "event", "access")

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("Code") != CODE_ACCESS_CONTROL:
            return
        data = event.get("Data") or {}
        event_type = ACCESS_AUTHORIZED if str(data.get("Status")) == "1" else ACCESS_DENIED
        self._trigger_event(
            event_type,
            {
                "card_no": data.get("CardNo"),
                "method": data.get("Method"),
                "name": data.get("Name"),
                "user_id": data.get("UserID"),
                "reader_id": data.get("ReaderID"),
                "type": data.get("Type"),
            },
        )
        self.async_write_ha_state()

"""Sensors: connection, last call (SPEC §4.1) and access log (SPEC §12.4)."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DahuaConfigEntry
from .const import CODE_VIDEO_TALK_LOG, KIND_VTO, STATE_CONNECTED, STATE_DISCONNECTED
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of both device kinds."""
    device = entry.runtime_data
    sensors: list[SensorEntity] = [DahuaConnectionSensor(device), DahuaLastCallSensor(device)]
    if device.kind == KIND_VTO:
        sensors.append(DahuaAccessLogSensor(device))
    async_add_entities(sensors)


class DahuaConnectionSensor(DahuaVtoEntity, SensorEntity):
    """``connected``/``disconnected``; always available (SPEC §4.1)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_CONNECTED, STATE_DISCONNECTED]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the connection sensor."""
        super().__init__(device, "sensor", "connection")

    @property
    def available(self) -> bool:
        """Report the disconnection instead of being unavailable."""
        return True

    @property
    def native_value(self) -> str:
        """Current session state."""
        return STATE_CONNECTED if self.device.connected else STATE_DISCONNECTED

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Model, serial and firmware (as the original sensor attributes)."""
        info = self.device.system_info
        return {
            "device_type": info.get("device_type"),
            "serial_number": info.get("serial_number"),
            "software_version": info.get("software_version"),
        }

    async def async_added_to_hass(self) -> None:
        """Expose our entity_id in bus events (SPEC §6)."""
        await super().async_added_to_hass()
        self.device.connection_entity_id = self.entity_id


class DahuaLastCallSensor(DahuaVtoEntity, RestoreSensor):
    """Timestamp of the last call from ``VideoTalkLog`` (SPEC §4.1)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the last call sensor."""
        super().__init__(device, "sensor", "last_call")
        self._attr_native_value: datetime | None = None
        self._attr_extra_state_attributes: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Restore the last call and listen for new ones."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None and isinstance(
            last.native_value, datetime
        ):
            self._attr_native_value = last.native_value
        if (state := await self.async_get_last_state()) is not None:
            self._attr_extra_state_attributes = {
                k: state.attributes[k]
                for k in ("end_state", "talk_time", "local_number", "missed")
                if k in state.attributes
            }
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_event, self._handle_event)
        )

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        if event.get("Code") != CODE_VIDEO_TALK_LOG:
            return
        data = event.get("Data") or {}
        begin = data.get("BeginTime")
        try:
            self._attr_native_value = datetime.fromtimestamp(int(begin), UTC)
        except (TypeError, ValueError):
            _LOGGER.debug("VideoTalkLog without valid BeginTime: %s", data)
            return
        end_state = data.get("EndState")
        self._attr_extra_state_attributes = {
            "end_state": end_state,
            "talk_time": data.get("TalkTime"),
            "local_number": data.get("LocalNumber"),
            "missed": end_state == "Missed",
        }
        self.async_write_ha_state()


class DahuaAccessLogSensor(DahuaVtoEntity, SensorEntity):
    """Last 60 accesses and calls (SPEC §12.4).

    State = time of the most recent entry; ``history`` holds the list and is
    excluded from the recorder (states over 16 KB are not stored). The data
    lives in the integration Store, so it is available while disconnected.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the access log sensor."""
        super().__init__(device, "sensor", "access_log")

    @property
    def available(self) -> bool:
        """The log is stored locally: always available."""
        return True

    @property
    def native_value(self) -> datetime | None:
        """Time of the latest entry."""
        entries = self.device.history.newest_first(1)
        return dt_util.parse_datetime(entries[0]["time"]) if entries else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Entries from the most recent."""
        return {"history": self.device.history.newest_first()}

    async def async_added_to_hass(self) -> None:
        """Refresh on every history change."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_history, self.async_write_ha_state)
        )

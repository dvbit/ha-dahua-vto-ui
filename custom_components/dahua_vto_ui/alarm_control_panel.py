"""VTH alarm on the ``CommGlobal`` table (SPEC §4.3, VTH only).

Replaces the "Arming/Disarming the VTH alarm" command examples of the
original README. Profiles follow Antori91/Home_Automation Dahua-VTH-SecPanel.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DahuaConfigEntry
from .const import (
    CONF_DISARM_CODE,
    DOMAIN,
    PROFILE_AT_HOME,
    PROFILE_CUSTOM,
    PROFILE_OUTDOOR,
    PROFILE_SLEEPING,
)
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity
from .rpc import DahuaError, error_text

_LOGGER = logging.getLogger(__name__)

# VTH profile ↔ HA state (SPEC §4.3).
PROFILE_TO_STATE: dict[str, AlarmControlPanelState] = {
    PROFILE_AT_HOME: AlarmControlPanelState.ARMED_HOME,
    PROFILE_OUTDOOR: AlarmControlPanelState.ARMED_AWAY,
    PROFILE_SLEEPING: AlarmControlPanelState.ARMED_NIGHT,
    PROFILE_CUSTOM: AlarmControlPanelState.ARMED_CUSTOM_BYPASS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the VTH alarm panel."""
    async_add_entities([DahuaVthAlarm(entry.runtime_data)])


class DahuaVthAlarm(DahuaVtoEntity, AlarmControlPanelEntity):
    """Alarm state of the VTH."""

    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
        | AlarmControlPanelEntityFeature.ARM_NIGHT
        | AlarmControlPanelEntityFeature.ARM_CUSTOM_BYPASS
    )

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the alarm panel."""
        super().__init__(device, "alarm_control_panel", "alarm")
        self._table: dict[str, Any] = {}

    async def async_added_to_hass(self) -> None:
        """Read the table now and on every reconnection; follow changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_config, self._handle_config)
        )
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_state, self._schedule_refresh)
        )
        await self._async_refresh()

    @callback
    def _schedule_refresh(self) -> None:
        if self.device.connected:
            self.hass.async_create_task(self._async_refresh())

    async def _async_refresh(self) -> None:
        try:
            self._table = await self.device.async_get_comm_global()
        except (DahuaError, TimeoutError) as err:
            _LOGGER.warning("%s: alarm state not readable: %s", self.device.name, error_text(err))
            return
        self.async_write_ha_state()

    @callback
    def _handle_config(self, table: dict[str, Any]) -> None:
        if "AlarmEnable" not in table:
            return
        old = self.alarm_state
        self._table = dict(table)
        if self.alarm_state != old:
            _LOGGER.info("%s: alarm changed to %s", self.device.name, self.alarm_state)
        self.async_write_ha_state()

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """``AlarmEnable`` false → disarmed, otherwise by profile."""
        if "AlarmEnable" not in self._table:
            return None
        if not self._table["AlarmEnable"]:
            return AlarmControlPanelState.DISARMED
        return PROFILE_TO_STATE.get(self._table.get("CurrentProfile", ""))

    @property
    def _disarm_code(self) -> str:
        """PIN required to disarm, empty = none (SPEC §12.1)."""
        return str(self.device.entry.options.get(CONF_DISARM_CODE) or "")

    @property
    def code_format(self) -> CodeFormat | None:
        """Keypad shown only when a PIN is set (SPEC §12.1)."""
        if not (code := self._disarm_code):
            return None
        return CodeFormat.NUMBER if code.isdigit() else CodeFormat.TEXT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Raw VTH profile."""
        return {"profile": self._table.get("CurrentProfile")}

    async def _async_apply(self, enable: bool, profile: str | None) -> None:
        """Re-read the table and write it back complete (SPEC §4.3)."""
        try:
            table = await self.device.async_get_comm_global()
            table["AlarmEnable"] = enable
            if profile is not None:
                table["CurrentProfile"] = profile
                table["ProfileEnable"] = True
            await self.device.async_set_comm_global(table)
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error("%s: alarm command failed: %s", self.device.name, error_text(err))
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._table = table
        _LOGGER.info("%s: alarm set to %s", self.device.name, self.alarm_state)
        self.async_write_ha_state()

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm (keep the current profile); check the PIN if set (SPEC §12.1)."""
        expected = self._disarm_code
        if expected and not hmac.compare_digest(str(code or ""), expected):
            _LOGGER.warning("%s: disarm refused, wrong or missing PIN", self.device.name)
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_code")
        await self._async_apply(False, None)

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm with the AtHome profile."""
        await self._async_apply(True, PROFILE_AT_HOME)

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm with the Outdoor profile."""
        await self._async_apply(True, PROFILE_OUTDOOR)

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        """Arm with the Sleeping profile."""
        await self._async_apply(True, PROFILE_SLEEPING)

    async def async_alarm_arm_custom_bypass(self, code: str | None = None) -> None:
        """Arm with the Custom profile."""
        await self._async_apply(True, PROFILE_CUSTOM)

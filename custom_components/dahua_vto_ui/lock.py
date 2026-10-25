"""Door locks (SPEC §4.2, VTO only).

Replaces the "template lock + timer" example of the original README: unlocking
opens the relay and the lock reports ``unlocked`` for ``unlock_period`` seconds.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import DahuaConfigEntry
from .const import (
    BKL_UNLOCK,
    CODE_ACCESS_CONTROL,
    CODE_BACK_KEY_LIGHT,
    CONF_LOCK_CHANNELS,
    CONF_SHORT_NUMBER,
    CONF_UNLOCK_PERIOD,
    DEFAULT_LOCK_CHANNELS,
    DEFAULT_SHORT_NUMBER,
    DEFAULT_UNLOCK_PERIOD,
    DOMAIN,
)
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity
from .rpc import DahuaError, error_text

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one lock per configured channel (option ``lock_channels``)."""
    device = entry.runtime_data
    channels = int(entry.options.get(CONF_LOCK_CHANNELS, DEFAULT_LOCK_CHANNELS))
    async_add_entities(DahuaDoorLock(device, ch) for ch in range(1, channels + 1))


class DahuaDoorLock(DahuaVtoEntity, LockEntity):
    """Momentary door relay shown as a lock."""

    def __init__(self, device: DahuaVtoDevice, channel: int) -> None:
        """Create the lock for ``channel`` (1-based)."""
        super().__init__(device, "lock", f"door_{channel}", "door")
        self._channel = channel
        self._attr_translation_placeholders = {"channel": str(channel)}
        self._attr_is_locked = True
        self._relock: CALLBACK_TYPE | None = None

    @property
    def _period(self) -> int:
        return int(self.device.entry.options.get(CONF_UNLOCK_PERIOD, DEFAULT_UNLOCK_PERIOD))

    async def async_added_to_hass(self) -> None:
        """Listen for unlocks made outside HA (SPEC §4.2)."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_event, self._handle_event)
        )
        self.async_on_remove(self._cancel_relock)

    @callback
    def _cancel_relock(self) -> None:
        if self._relock is not None:
            self._relock()
            self._relock = None

    @callback
    def _mark_unlocked(self) -> None:
        """Show ``unlocked`` and schedule the automatic relock."""
        self._cancel_relock()
        self._attr_is_locked = False
        self.async_write_ha_state()

        @callback
        def _relock(_now: Any) -> None:
            self._relock = None
            self._attr_is_locked = True
            self.async_write_ha_state()

        self._relock = async_call_later(self.hass, self._period, _relock)

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        code = event.get("Code")
        data = event.get("Data") or {}
        if (code == CODE_BACK_KEY_LIGHT and str(data.get("State")) == str(BKL_UNLOCK)) or (
            code == CODE_ACCESS_CONTROL and str(data.get("Status")) == "1"
        ):
            # The device does not say which relay: all channels (SPEC §4.2).
            self._mark_unlocked()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Open the relay of this channel."""
        short_number = self.device.entry.options.get(CONF_SHORT_NUMBER, DEFAULT_SHORT_NUMBER)
        try:
            await self.device.async_open_door(self._channel, short_number)
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error(
                "%s: opening door %s failed: %s", self.device.name, self._channel, error_text(err)
            )
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        self._mark_unlocked()

    async def async_lock(self, **kwargs: Any) -> None:
        """Do nothing: the relay locks by itself (SPEC §4.2)."""

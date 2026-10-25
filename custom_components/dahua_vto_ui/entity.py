"""Base entity shared by all platforms (SPEC §4)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import DOMAIN
from .device import DahuaVtoDevice


class DahuaVtoEntity(Entity):
    """Entity bound to one device, available while connected.

    Names are localized through ``translation_key`` while the entity_id is
    suggested in English (``<platform>.<device>_<key>``) so it does not depend
    on the UI language at registration time (SPEC §4).
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        device: DahuaVtoDevice,
        platform: str,
        key: str,
        translation_key: str | None = None,
    ) -> None:
        """Set unique_id, translation key, suggested entity_id and device info."""
        self.device = device
        uid = device.entry.unique_id or device.entry.entry_id
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_translation_key = translation_key or key
        self.entity_id = f"{platform}.{slugify(device.name)}_{key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, uid)})

    @property
    def available(self) -> bool:
        """Available only while the session is up (SPEC §4)."""
        return self.device.connected

    async def async_added_to_hass(self) -> None:
        """Follow connection changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.device.async_subscribe(self.device.signal_state, self.async_write_ha_state)
        )

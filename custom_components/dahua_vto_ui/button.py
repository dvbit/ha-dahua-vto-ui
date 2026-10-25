"""Buttons: reboot (both kinds) and cancel call (VTO) (SPEC §4.1, §4.2)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DahuaConfigEntry
from .const import DOMAIN, KIND_VTO
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity
from .rpc import DahuaError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons supported by the device kind."""
    device = entry.runtime_data
    buttons = [
        DahuaButton(
            device, "reboot", device.async_reboot, ButtonDeviceClass.RESTART, EntityCategory.CONFIG
        )
    ]
    if device.kind == KIND_VTO:
        buttons.append(DahuaButton(device, "cancel_call", device.async_cancel_call))
    async_add_entities(buttons)


class DahuaButton(DahuaVtoEntity, ButtonEntity):
    """Button running one device command."""

    def __init__(
        self,
        device: DahuaVtoDevice,
        key: str,
        action: Callable[[], Awaitable[None]],
        device_class: ButtonDeviceClass | None = None,
        category: EntityCategory | None = None,
    ) -> None:
        """Create the button for ``action``."""
        super().__init__(device, "button", key)
        self._action = action
        self._attr_device_class = device_class
        self._attr_entity_category = category

    async def async_press(self) -> None:
        """Run the command, translate failures."""
        try:
            await self._action()
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error("%s: %s failed: %s", self.device.name, self._attr_translation_key, err)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

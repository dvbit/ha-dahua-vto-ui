"""Dahua VTO UI: Dahua VTO/VTH intercoms configured from the UI (SPEC.md).

Fork of myhomeiot/DahuaVTO (GPL-3.0). One config entry per device.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import CONF_DEVICE_KIND, DOMAIN, KIND_VTH
from .device import DahuaVtoDevice
from .rpc import DahuaAuthError, DahuaError
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

type DahuaConfigEntry = ConfigEntry[DahuaVtoDevice]

# Platforms per device kind (SPEC §4).
PLATFORMS_VTO: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.LOCK,
    Platform.SENSOR,
]
PLATFORMS_VTH: list[Platform] = [
    Platform.ALARM_CONTROL_PANEL,
    Platform.BUTTON,
    Platform.SENSOR,
]

# UI-only integration (SPEC §1.2).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _platforms(entry: ConfigEntry) -> list[Platform]:
    return PLATFORMS_VTH if entry.data.get(CONF_DEVICE_KIND) == KIND_VTH else PLATFORMS_VTO


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain services once (SPEC §5)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DahuaConfigEntry) -> bool:
    """Connect to the device and forward the platforms (SPEC §3.4)."""
    device = DahuaVtoDevice(hass, entry)
    try:
        await device.async_connect()
    except DahuaAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="invalid_auth"
        ) from err
    except (DahuaError, TimeoutError, OSError) as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"error": str(err)},
        ) from err

    # Register the device now so services can resolve it before entities exist.
    info = device.system_info
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
        manufacturer="Dahua",
        model=info.get("device_type"),
        name=entry.title,
        serial_number=info.get("serial_number"),
        sw_version=info.get("software_version"),
    )
    device.device_id = dev.id
    entry.runtime_data = device

    await hass.config_entries.async_forward_entry_setups(entry, _platforms(entry))
    await device.async_start()
    _LOGGER.info("%s ready (%s, %s)", entry.title, info.get("device_type"), device.kind.upper())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DahuaConfigEntry) -> bool:
    """Close the session and unload the platforms."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, _platforms(entry))
    if unloaded:
        await entry.runtime_data.async_stop()
        _LOGGER.info("%s unloaded", entry.title)
    return unloaded

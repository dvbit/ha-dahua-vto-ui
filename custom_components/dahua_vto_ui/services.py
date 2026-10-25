"""Domain services with response (SPEC §5).

Same commands as the original integration, targeted by ``device_id`` instead
of the sensor entity, and returning the device answer directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import (
    ATTR_CHANNEL,
    ATTR_DEVICE_ID,
    ATTR_EVENT,
    ATTR_INSTANCE_PARAMS,
    ATTR_LIMIT,
    ATTR_METHOD,
    ATTR_PARAMS,
    ATTR_TAG,
    ATTR_TIMEOUT,
    CONF_SHORT_NUMBER,
    DEFAULT_SHORT_NUMBER,
    DOMAIN,
    HISTORY_SIZE,
    MAX_LOCK_CHANNELS,
    SERVICE_DEFAULT_TIMEOUT,
    SERVICE_GET_ACCESS_LOG,
    SERVICE_OPEN_DOOR,
    SERVICE_SEND_COMMAND,
    SERVICE_SEND_INSTANCE_COMMAND,
)
from .rpc import DahuaError, error_text

if TYPE_CHECKING:
    from .device import DahuaVtoDevice

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = vol.All(vol.Coerce(int), vol.Range(min=1, max=60))

SCHEMA_OPEN_DOOR = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_CHANNEL, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=MAX_LOCK_CHANNELS)
        ),
        vol.Optional(CONF_SHORT_NUMBER): cv.string,
    }
)
SCHEMA_SEND_COMMAND = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_METHOD): vol.Any(cv.string, dict),
        vol.Optional(ATTR_PARAMS): object,
        vol.Optional(ATTR_EVENT, default=True): cv.boolean,
        vol.Optional(ATTR_TAG): object,
        vol.Optional(ATTR_TIMEOUT, default=SERVICE_DEFAULT_TIMEOUT): _TIMEOUT,
    }
)
SCHEMA_SEND_INSTANCE_COMMAND = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_METHOD): cv.string,
        vol.Optional(ATTR_INSTANCE_PARAMS): object,
        vol.Optional(ATTR_PARAMS): object,
        vol.Optional(ATTR_EVENT, default=True): cv.boolean,
        vol.Optional(ATTR_TAG): object,
        vol.Optional(ATTR_TIMEOUT, default=SERVICE_DEFAULT_TIMEOUT): _TIMEOUT,
    }
)


SCHEMA_GET_ACCESS_LOG = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Optional(ATTR_LIMIT, default=HISTORY_SIZE): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=HISTORY_SIZE)
        ),
    }
)


def _get_device(
    hass: HomeAssistant, device_id: str, require_connected: bool = True
) -> DahuaVtoDevice:
    """Resolve a device_id to our runtime device (SPEC §5.1, §5.2)."""
    dev = dr.async_get(hass).async_get(device_id)
    entry = hass.config_entries.async_get_entry(dev.config_entry_id) if dev else None
    if entry is not None and entry.domain == DOMAIN and entry.state is ConfigEntryState.LOADED:
        device: DahuaVtoDevice = entry.runtime_data
        if require_connected and not device.connected:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_connected",
                translation_placeholders={"name": entry.title},
            )
        return device
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_device",
        translation_placeholders={"device_id": device_id},
    )


def _command_error(err: Exception) -> HomeAssistantError:
    """Translate client errors (SPEC §5.2)."""
    if isinstance(err, TimeoutError):
        return HomeAssistantError(translation_domain=DOMAIN, translation_key="timeout")
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"error": str(err)},
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of SPEC §5 and ``get_access_log`` (SPEC §12.4)."""

    async def open_door(call: ServiceCall) -> None:
        device = _get_device(hass, call.data[ATTR_DEVICE_ID])
        short_number = call.data.get(CONF_SHORT_NUMBER) or device.entry.options.get(
            CONF_SHORT_NUMBER, DEFAULT_SHORT_NUMBER
        )
        try:
            await device.async_open_door(call.data[ATTR_CHANNEL], short_number)
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error("%s: open_door failed: %s", device.name, error_text(err))
            raise _command_error(err) from err

    async def send_command(call: ServiceCall) -> ServiceResponse:
        device = _get_device(hass, call.data[ATTR_DEVICE_ID])
        method = call.data[ATTR_METHOD]
        try:
            response = await device.async_send_command(
                method, call.data.get(ATTR_PARAMS), call.data[ATTR_TIMEOUT]
            )
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error("%s: send_command %s failed: %s", device.name, method, error_text(err))
            raise _command_error(err) from err
        _LOGGER.info("%s: command %s sent", device.name, method)
        return _finish(device, method, response, call.data)

    async def send_instance_command(call: ServiceCall) -> ServiceResponse:
        device = _get_device(hass, call.data[ATTR_DEVICE_ID])
        method = call.data[ATTR_METHOD]
        try:
            response = await device.async_send_instance_command(
                method,
                call.data.get(ATTR_INSTANCE_PARAMS),
                call.data.get(ATTR_PARAMS),
                call.data[ATTR_TIMEOUT],
            )
        except (DahuaError, TimeoutError) as err:
            _LOGGER.error(
                "%s: send_instance_command %s failed: %s", device.name, method, error_text(err)
            )
            raise _command_error(err) from err
        _LOGGER.info("%s: instance command %s sent", device.name, method)
        return _finish(device, method, response, call.data)

    async def get_access_log(call: ServiceCall) -> ServiceResponse:
        """Return the stored history, newest first (SPEC §12.4)."""
        device = _get_device(hass, call.data[ATTR_DEVICE_ID], require_connected=False)
        entries = device.history.newest_first(call.data[ATTR_LIMIT])
        _LOGGER.debug("%s: access log requested, %d entries", device.name, len(entries))
        return {"entries": entries}

    hass.services.async_register(DOMAIN, SERVICE_OPEN_DOOR, open_door, SCHEMA_OPEN_DOOR)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_ACCESS_LOG,
        get_access_log,
        SCHEMA_GET_ACCESS_LOG,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_COMMAND,
        send_command,
        SCHEMA_SEND_COMMAND,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_INSTANCE_COMMAND,
        send_instance_command,
        SCHEMA_SEND_INSTANCE_COMMAND,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _finish(
    device: DahuaVtoDevice, method: Any, response: dict[str, Any], data: dict[str, Any]
) -> ServiceResponse:
    """Fire the bus event if requested and return the clean answer (SPEC §5, §6)."""
    if data[ATTR_EVENT]:
        device.fire_result(method, response, data.get(ATTR_TAG))
    return {k: v for k, v in response.items() if k not in ("id", "session")}

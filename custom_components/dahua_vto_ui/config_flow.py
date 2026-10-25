"""Config, reauth and options flows (SPEC §2)."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    CONF_DEVICE_KIND,
    CONF_HTTP_PORT,
    CONF_LOCK_CHANNELS,
    CONF_MODEL,
    CONF_RTSP_PORT,
    CONF_SERIAL,
    CONF_SHORT_NUMBER,
    CONF_STREAM,
    CONF_UNLOCK_PERIOD,
    DEFAULT_HTTP_PORT,
    DEFAULT_LOCK_CHANNELS,
    DEFAULT_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_SHORT_NUMBER,
    DEFAULT_STREAM,
    DEFAULT_TIMEOUT,
    DEFAULT_UNLOCK_PERIOD,
    DEFAULT_USERNAME,
    DOMAIN,
    KIND_VTO,
    MAX_LOCK_CHANNELS,
    MAX_TIMEOUT,
    MAX_UNLOCK_PERIOD,
    MIN_TIMEOUT,
    STREAM_MAIN,
    STREAM_SUB,
)
from .device import async_fetch_system_info, detect_kind
from .rpc import DahuaAuthError, DahuaError, DahuaRpcClient

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


_PORT = NumberSelector(NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX))


async def async_validate(data: Mapping[str, Any]) -> dict[str, Any]:
    """Log in once and read the system info (SPEC §2.1).

    Raises DahuaAuthError / DahuaError / TimeoutError.
    """
    client = DahuaRpcClient(
        data[CONF_HOST],
        int(data[CONF_PORT]),
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        DEFAULT_TIMEOUT,
    )
    try:
        await client.connect()
        return await async_fetch_system_info(client)
    finally:
        client.close()


async def _try_validate(data: Mapping[str, Any]) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Wrap :func:`async_validate` into (info, errors)."""
    try:
        info = await async_validate(data)
    except DahuaAuthError:
        _LOGGER.warning("Login rejected by %s", data[CONF_HOST])
        return None, {"base": "invalid_auth"}
    except (DahuaError, TimeoutError, OSError) as err:
        _LOGGER.warning("Cannot connect to %s: %s", data[CONF_HOST], err)
        return None, {"base": "cannot_connect"}
    except Exception:
        _LOGGER.exception("Unexpected error validating %s", data[CONF_HOST])
        return None, {"base": "unknown"}
    if not info.get("serial_number"):
        _LOGGER.warning("%s did not report a serial number", data[CONF_HOST])
        return None, {"base": "no_serial"}
    return info, {}


class DahuaVtoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Dahua VTO/VTH (SPEC §2.1-2.4)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DahuaVtoOptionsFlow:
        """Return the options flow."""
        return DahuaVtoOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Host, port, credentials; validated with a real login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[CONF_PORT] = int(user_input[CONF_PORT])
            info, errors = await _try_validate(user_input)
            if info is not None:
                await self.async_set_unique_id(info["serial_number"])
                # Same device on a new address: update and stop (SPEC §2.2).
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    }
                )
                model = info.get("device_type") or "Dahua"
                kind = detect_kind(model)
                if kind == KIND_VTO and not model.upper().startswith("VTO"):
                    _LOGGER.warning("Model %s not recognised, treated as VTO", model)
                title = (user_input.pop(CONF_NAME, "") or "").strip() or model
                _LOGGER.info("Adding %s (%s) at %s", title, model, user_input[CONF_HOST])
                return self.async_create_entry(
                    title=title,
                    data={
                        **user_input,
                        CONF_DEVICE_KIND: kind,
                        CONF_MODEL: model,
                        CONF_SERIAL: info["serial_number"],
                    },
                )

        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): _PORT,
                vol.Required(
                    CONF_USERNAME, default=defaults.get(CONF_USERNAME, DEFAULT_USERNAME)
                ): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Credentials rejected (SPEC §2.4)."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask username and password again; same serial number required."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {**entry.data, **user_input}
            info, errors = await _try_validate(data)
            if info is not None:
                await self.async_set_unique_id(info["serial_number"])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                _LOGGER.info("Credentials updated for %s", entry.title)
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_USERNAME, default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
                ): str,
                vol.Required(CONF_PASSWORD): _PASSWORD,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": entry.title},
        )


class DahuaVtoOptionsFlow(OptionsFlowWithReload):
    """Runtime options, reload on save (SPEC §2.5)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """One form; VTO-only fields hidden for VTH."""
        if user_input is not None:
            for key in (
                CONF_TIMEOUT,
                CONF_LOCK_CHANNELS,
                CONF_UNLOCK_PERIOD,
                CONF_RTSP_PORT,
                CONF_HTTP_PORT,
            ):
                if key in user_input:
                    user_input[key] = int(user_input[key])
            _LOGGER.info("Options updated for %s: %s", self.config_entry.title, user_input)
            return self.async_create_entry(data=user_input)

        o = self.config_entry.options
        fields: dict[Any, Any] = {
            vol.Required(CONF_TIMEOUT, default=o.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)): (
                NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_TIMEOUT,
                        max=MAX_TIMEOUT,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
            )
        }
        if self.config_entry.data.get(CONF_DEVICE_KIND, KIND_VTO) == KIND_VTO:
            fields.update(
                {
                    vol.Required(
                        CONF_LOCK_CHANNELS,
                        default=o.get(CONF_LOCK_CHANNELS, DEFAULT_LOCK_CHANNELS),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=MAX_LOCK_CHANNELS, mode=NumberSelectorMode.BOX
                        )
                    ),
                    vol.Required(
                        CONF_UNLOCK_PERIOD,
                        default=o.get(CONF_UNLOCK_PERIOD, DEFAULT_UNLOCK_PERIOD),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=MAX_UNLOCK_PERIOD,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Required(
                        CONF_SHORT_NUMBER,
                        default=o.get(CONF_SHORT_NUMBER, DEFAULT_SHORT_NUMBER),
                    ): str,
                    vol.Required(
                        CONF_RTSP_PORT, default=o.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)
                    ): _PORT,
                    vol.Required(
                        CONF_HTTP_PORT, default=o.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)
                    ): _PORT,
                    vol.Required(
                        CONF_STREAM, default=o.get(CONF_STREAM, DEFAULT_STREAM)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[STREAM_MAIN, STREAM_SUB],
                            mode=SelectSelectorMode.LIST,
                            translation_key=CONF_STREAM,
                        )
                    ),
                }
            )
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))

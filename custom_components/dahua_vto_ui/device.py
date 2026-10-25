"""Runtime object of one Dahua VTO/VTH config entry (SPEC §3, §5, §6).

Owns the RPC client, the reconnect loop, the cached device data and the
fan-out of notifications to entities (dispatcher) and to the event bus.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_TIMEOUT,
    CONF_USERNAME,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.dispatcher import (
    async_dispatcher_connect,
    async_dispatcher_send,
)
from homeassistant.util import dt as dt_util

from .const import (
    BKL_KNOWN,
    CODE_ACCESS_CONTROL,
    CODE_BACK_KEY_LIGHT,
    CODE_CALL_SNAP,
    CODE_CONFIG_CHANGE,
    CODE_FINGERPRINT_CHECK,
    CODE_VIDEO_TALK_LOG,
    CONF_DEVICE_KIND,
    CONF_FINGERPRINT_PERSONS,
    CONFIG_COMM_GLOBAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EVENT_BUS,
    EVENT_HISTORY_SIZE,
    KIND_VTH,
    KIND_VTO,
)
from .history import AccessHistory
from .rpc import DahuaAuthError, DahuaCommandError, DahuaError, DahuaRpcClient, error_text

_LOGGER = logging.getLogger(__name__)


def detect_kind(device_type: str | None) -> str:
    """Return ``vth`` for VTH models, ``vto`` otherwise (SPEC §2.3)."""
    return KIND_VTH if (device_type or "").upper().startswith("VTH") else KIND_VTO


async def async_fetch_system_info(client: DahuaRpcClient) -> dict[str, Any]:
    """Read model, serial and firmware from a logged-in client (SPEC §2.1)."""
    info = (await client.request("magicBox.getSystemInfo")).get("params") or {}
    data: dict[str, Any] = {
        "device_type": info.get("deviceType"),
        "serial_number": info.get("serialNumber"),
        "software_version": None,
    }
    try:
        sw = await client.request("magicBox.getSoftwareVersion", check=False)
        data["software_version"] = ((sw.get("params") or {}).get("version") or {}).get("Version")
    except (DahuaError, TimeoutError):
        _LOGGER.debug("Software version not available on %s", client.host)
    return data


class DahuaVtoDevice:
    """Connection manager and command facade for one device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Prepare the device; call :meth:`async_start` to connect."""
        self.hass = hass
        self.entry = entry
        self.kind: str = entry.data.get(CONF_DEVICE_KIND, KIND_VTO)
        self.system_info: dict[str, Any] = {}
        self.client: DahuaRpcClient | None = None
        self.connected = False
        self.connected_since: str | None = None
        self.connection_entity_id: str | None = None  # set by the sensor (SPEC §6)
        self.device_id: str | None = None  # set in async_setup_entry
        self.events: deque[dict[str, Any]] = deque(maxlen=EVENT_HISTORY_SIZE)
        self.history = AccessHistory(hass, entry.entry_id)  # SPEC §12.4
        self._disconnected: asyncio.Event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # -------------------------------------------------------------- signals
    @property
    def signal_state(self) -> str:
        """Dispatcher signal: connection state changed."""
        return f"{DOMAIN}_{self.entry.entry_id}_state"

    @property
    def signal_event(self) -> str:
        """Dispatcher signal: device event received."""
        return f"{DOMAIN}_{self.entry.entry_id}_event"

    @property
    def signal_config(self) -> str:
        """Dispatcher signal: configuration table changed."""
        return f"{DOMAIN}_{self.entry.entry_id}_config"

    @property
    def signal_doorbell(self) -> str:
        """Dispatcher signal: interpreted doorbell event (SPEC §4.2)."""
        return f"{DOMAIN}_{self.entry.entry_id}_doorbell"

    @property
    def signal_access(self) -> str:
        """Dispatcher signal: interpreted access event (SPEC §12.2-12.3)."""
        return f"{DOMAIN}_{self.entry.entry_id}_access"

    @property
    def signal_history(self) -> str:
        """Dispatcher signal: history changed (SPEC §12.4)."""
        return f"{DOMAIN}_{self.entry.entry_id}_history"

    @property
    def fingerprint_persons(self) -> dict[str, str]:
        """Fingerprint ID → person entity_id from the options (SPEC §12.3)."""
        return dict(self.entry.options.get(CONF_FINGERPRINT_PERSONS) or {})

    @callback
    def async_subscribe(self, signal: str, target: Callable[..., None]) -> CALLBACK_TYPE:
        """Subscribe an entity callback to one of the signals."""
        return async_dispatcher_connect(self.hass, signal, target)

    # -------------------------------------------------------------- options
    @property
    def timeout(self) -> int:
        """Command timeout and reconnect delay (SPEC §2.5)."""
        return int(self.entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT))

    @property
    def name(self) -> str:
        """Device name = entry title."""
        return self.entry.title

    # ------------------------------------------------------------ lifecycle
    def _new_client(self) -> DahuaRpcClient:
        data = self.entry.data
        return DahuaRpcClient(
            data[CONF_HOST],
            data[CONF_PORT],
            data[CONF_USERNAME],
            data[CONF_PASSWORD],
            self.timeout,
            on_event=self._handle_event,
            on_config_change=self._handle_config_change,
            on_disconnect=self._handle_disconnect,
        )

    async def async_connect(self) -> None:
        """Connect, log in, read system info and subscribe (SPEC §3.1-3.2).

        Raises DahuaAuthError / DahuaError to the caller.
        """
        client = self._new_client()
        await client.connect()
        try:
            self.system_info = await async_fetch_system_info(client)
            await client.attach()
        except BaseException:
            client.close()
            raise
        self.client = client
        self._disconnected.clear()
        self._set_connected(True)

    async def async_start(self) -> None:
        """Start the background reconnect loop (first connection already done)."""
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(), f"{DOMAIN}_{self.entry.entry_id}"
        )

    async def _run(self) -> None:
        """Wait for disconnections and reconnect after ``timeout`` (SPEC §3.3)."""
        while not self._stopping:
            await self._disconnected.wait()
            if self._stopping:
                return
            await asyncio.sleep(self.timeout)
            try:
                await self.async_connect()
            except DahuaAuthError as err:
                _LOGGER.error("%s: credentials rejected (%s), reauth needed", self.name, err)
                self.entry.async_start_reauth(self.hass)
                return
            except (DahuaError, TimeoutError, OSError) as err:
                _LOGGER.warning(
                    "%s: reconnection failed (%s), retry in %s s",
                    self.name,
                    error_text(err),
                    self.timeout,
                )

    async def async_stop(self) -> None:
        """Close the session and stop the loop (unload)."""
        self._stopping = True
        self._disconnected.set()
        if self.client is not None:
            self.client.close()
            self.client = None
        if self._task is not None:
            self._task.cancel()
        self.connected = False
        await self.history.async_flush()

    # --------------------------------------------------------------- events
    @callback
    def _set_connected(self, connected: bool) -> None:
        if connected == self.connected:
            return
        self.connected = connected
        self.connected_since = dt_util.utcnow().isoformat() if connected else None
        if connected:
            _LOGGER.info(
                "%s connected (%s, %s)",
                self.name,
                self.system_info.get("device_type"),
                self.entry.data[CONF_HOST],
            )
        else:
            _LOGGER.info("%s disconnected", self.name)
        async_dispatcher_send(self.hass, self.signal_state)

    @callback
    def _handle_disconnect(self, exc: Exception | None) -> None:
        """Handle an unexpected socket close reported by the client."""
        self.client = None
        self._set_connected(False)
        if not self._stopping:
            _LOGGER.warning(
                "%s: connection lost (%s), reconnecting in %s s",
                self.name,
                exc or "closed by device",
                self.timeout,
            )
            self._disconnected.set()

    def _bus_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Add the identifiers of SPEC §6 to a bus event payload."""
        payload["device_id"] = self.device_id
        payload["entry_id"] = self.entry.entry_id
        payload["entity_id"] = self.connection_entity_id
        return payload

    @callback
    def _handle_event(self, event: dict[str, Any]) -> None:
        """Device event: keep history, fire bus event, notify entities."""
        _LOGGER.debug("%s event %s %s", self.name, event.get("Code"), event.get("Data"))
        self.events.append({"received": dt_util.utcnow().isoformat(), **event})
        self.hass.bus.async_fire(EVENT_BUS, self._bus_payload(dict(event)))
        async_dispatcher_send(self.hass, self.signal_event, event)
        self._interpret(event)

    @callback
    def _interpret(self, event: dict[str, Any]) -> None:
        """Feed the shared history and send derived signals (SPEC §12)."""
        code = event.get("Code")
        data = event.get("Data") or {}
        history = self.history
        if code == CODE_BACK_KEY_LIGHT:
            state = data.get("State")
            if str(state) not in {str(s) for s in BKL_KNOWN}:
                _LOGGER.warning("%s: unknown BackKeyLight state %s", self.name, state)
            if (event_type := history.handle_back_key_light(data)) is None:
                return
            _LOGGER.debug("%s doorbell %s", self.name, event_type)
            async_dispatcher_send(
                self.hass,
                self.signal_doorbell,
                event_type,
                {"state": state, "utc": data.get("UTC")},
            )
        elif code == CODE_CALL_SNAP:
            history.handle_call_snap(data)
        elif code == CODE_VIDEO_TALK_LOG:
            history.handle_video_talk_log(data)
        elif code == CODE_FINGERPRINT_CHECK:
            history.handle_fingerprint_check(data)
            return
        elif code == CODE_ACCESS_CONTROL:
            entry = history.handle_access_control(data, self.fingerprint_persons)
            _LOGGER.debug("%s access %s", self.name, entry)
            async_dispatcher_send(self.hass, self.signal_access, entry)
        else:
            return
        async_dispatcher_send(self.hass, self.signal_history)

    @callback
    def _handle_config_change(self, table: dict[str, Any]) -> None:
        """Config table change (e.g. CommGlobal), same payload as original."""
        _LOGGER.debug("%s config change %s", self.name, table)
        payload = dict(table)
        payload["Code"] = CODE_CONFIG_CHANGE
        self.hass.bus.async_fire(EVENT_BUS, self._bus_payload(payload))
        async_dispatcher_send(self.hass, self.signal_config, table)

    # ------------------------------------------------------------- commands
    def _require_client(self) -> DahuaRpcClient:
        if self.client is None or not self.connected:
            raise DahuaError("not connected")
        return self.client

    def fire_result(self, method: Any, response: dict[str, Any], tag: Any) -> None:
        """Fire a command result on the bus like the original (SPEC §6)."""
        payload = {k: v for k, v in response.items() if k not in ("id", "session")}
        payload["method"] = method
        if tag:
            payload["tag"] = tag
        self.hass.bus.async_fire(EVENT_BUS, self._bus_payload(payload))

    async def async_send_command(
        self, method: Any, params: Any, timeout: float | None = None
    ) -> dict[str, Any]:
        """Raw command (service ``send_command``)."""
        return await self._require_client().request(method, params, timeout=timeout, check=False)

    async def async_send_instance_command(
        self,
        method: str,
        instance_params: Any,
        params: Any,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Raw instance command (service ``send_instance_command``)."""
        return await self._require_client().instance_request(
            method, instance_params, params, timeout=timeout
        )

    async def async_open_door(self, channel: int, short_number: str) -> None:
        """Open a door relay (original ``open_door``, SPEC §4.2, §5)."""
        response = await self._require_client().instance_request(
            "accessControl.openDoor",
            {"channel": channel - 1},
            {"DoorIndex": 0, "ShortNumber": short_number},
        )
        if response.get("error") or response.get("result") is False:
            raise DahuaCommandError("accessControl.openDoor", response)
        _LOGGER.info("%s: door %s opened", self.name, channel)

    async def async_reboot(self) -> None:
        """Reboot the device (SPEC §4.1)."""
        await self._require_client().request("magicBox.reboot")
        _LOGGER.info("%s: reboot requested", self.name)

    async def async_cancel_call(self) -> None:
        """Hang up the current call (SPEC §4.2)."""
        await self._require_client().request("console.runCmd", {"command": "hc"})
        _LOGGER.info("%s: call cancelled", self.name)

    async def async_get_comm_global(self) -> dict[str, Any]:
        """Read the VTH alarm table (SPEC §4.3)."""
        response = await self._require_client().request(
            "configManager.getConfig", {"name": CONFIG_COMM_GLOBAL}
        )
        return dict((response.get("params") or {}).get("table") or {})

    async def async_set_comm_global(self, table: dict[str, Any]) -> None:
        """Write the full VTH alarm table (SPEC §4.3)."""
        await self._require_client().request(
            "configManager.setConfig", {"table": table, "name": CONFIG_COMM_GLOBAL}
        )

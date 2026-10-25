"""Dahua DHIP JSON-RPC client (SPEC §1.3, §3).

Protocol taken from the original integration (myhomeiot/DahuaVTO, GPL-3.0):
every frame is a 32-byte little-endian header followed by a JSON body.
Login is a two-step challenge: the first ``global.login`` is answered with an
error carrying ``random``/``realm``; the second sends an MD5 digest.

This module has no Home Assistant dependency so it can be tested in isolation.
Improvements over the original:
- several commands may be in flight at once (futures keyed by request id, §3.5);
- authentication errors are reported with a dedicated exception (§3.3);
- the password digest is masked in debug logs (§7).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
import hashlib
import json
import logging
import struct
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Header: proto (Q), session (L), request id (L), length (Q), length (Q).
DHIP_PROTO = 0x5049484400000020
DHIP_HEADER = "<QLLQQ"
DHIP_HEADER_LEN = struct.calcsize(DHIP_HEADER)

# Error code returned by the first login step (challenge).
DHIP_REALM_CHALLENGE = 268632079

LOGIN_BASE_PARAMS = {"clientType": "", "ipAddr": "(null)", "loginType": "Direct"}
DEFAULT_KEEPALIVE = 60

EventCallback = Callable[[dict[str, Any]], None]
ConfigCallback = Callable[[dict[str, Any]], None]
DisconnectCallback = Callable[[Exception | None], None]


class DahuaError(Exception):
    """Base error of the client."""


class DahuaConnectionError(DahuaError):
    """The device cannot be reached or the connection dropped."""


class DahuaAuthError(DahuaError):
    """The device rejected the credentials (SPEC §2.4, §3.3)."""


class DahuaCommandError(DahuaError):
    """The device answered a command with an error or ``result: false``."""

    def __init__(self, method: str, response: dict[str, Any]) -> None:
        """Keep the raw response for the caller."""
        error = response.get("error") or {}
        super().__init__(
            f"{method}: {error.get('code', '-')} {error.get('message', 'result false')}"
        )
        self.method = method
        self.response = response


def error_text(err: BaseException) -> str:
    """Readable error for logs: timeouts have an empty str() (SPEC §12.5)."""
    if isinstance(err, TimeoutError):
        return "timeout"
    return str(err) or type(err).__name__


def hash_password(username: str, password: str, random: str, realm: str) -> str:
    """Return the Dahua login digest: MD5(user:random:MD5(user:realm:pass))."""
    inner = (
        hashlib.md5(f"{username}:{realm}:{password}".encode()).hexdigest().upper()  # noqa: S324
    )
    return hashlib.md5(f"{username}:{random}:{inner}".encode()).hexdigest().upper()  # noqa: S324


class _DhipProtocol(asyncio.Protocol):
    """Low level framing: split the TCP stream into JSON messages."""

    def __init__(self, client: DahuaRpcClient) -> None:
        self._client = client
        self._buffer = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._client.transport = transport  # type: ignore[assignment]

    def connection_lost(self, exc: Exception | None) -> None:
        self._client.handle_connection_lost(exc)

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        while len(self._buffer) >= DHIP_HEADER_LEN:
            proto, _session, _req, length, _len2 = struct.unpack_from(DHIP_HEADER, self._buffer)
            if proto != DHIP_PROTO:
                _LOGGER.warning("Unexpected protocol header, closing connection")
                self._client.close()
                return
            end = DHIP_HEADER_LEN + length
            if end > len(self._buffer):
                return  # wait for the rest of the frame
            body = self._buffer[DHIP_HEADER_LEN:end].decode("utf-8", "ignore")
            self._buffer = self._buffer[end:]
            try:
                message = json.loads(body)
            except ValueError:
                _LOGGER.debug("<<< invalid JSON ignored: %s", body.rstrip())
                continue
            _LOGGER.debug("<<< %s", body.rstrip())
            self._client.handle_message(message)


class DahuaRpcClient:
    """Persistent DHIP session with login, keep-alive and notifications."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: float,
        *,
        on_event: EventCallback | None = None,
        on_config_change: ConfigCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
    ) -> None:
        """Store connection parameters; nothing is opened yet."""
        self.host = host
        self.port = port
        self.username = username
        self._password = password
        self.timeout = timeout
        self._on_event = on_event
        self._on_config_change = on_config_change
        self._on_disconnect = on_disconnect

        self.transport: asyncio.Transport | None = None
        self.session_id = 0
        self.keepalive_interval = DEFAULT_KEEPALIVE
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._keepalive_task: asyncio.Task[None] | None = None
        self._closed = False

    # ------------------------------------------------------------------ state
    @property
    def connected(self) -> bool:
        """Return True while the TCP session is open."""
        return self.transport is not None and not self.transport.is_closing()

    # ---------------------------------------------------------------- connect
    async def connect(self) -> dict[str, Any]:
        """Open the socket and log in (SPEC §3.1). Return login params."""
        loop = asyncio.get_running_loop()
        _LOGGER.debug("Connecting to %s:%s as %s", self.host, self.port, self.username)
        try:
            async with asyncio.timeout(self.timeout):
                await loop.create_connection(lambda: _DhipProtocol(self), self.host, self.port)
        except (OSError, TimeoutError) as err:
            raise DahuaConnectionError(f"Cannot connect to {self.host}:{self.port}: {err}") from err
        try:
            params = await self._login()
        except BaseException:
            self.close()
            raise
        self.keepalive_interval = int(params.get("keepAliveInterval") or DEFAULT_KEEPALIVE)
        self._keepalive_task = loop.create_task(self._keepalive_loop())
        return params

    async def _login(self) -> dict[str, Any]:
        """Two-step challenge login (original integration, ``receive``)."""
        first = await self.request("global.login", dict(LOGIN_BASE_PARAMS), check=False)
        error = first.get("error") or {}
        if error.get("code") != DHIP_REALM_CHALLENGE:
            raise DahuaConnectionError(f"Unexpected login answer: {error or first}")
        self.session_id = first.get("session", 0)
        challenge = first.get("params") or {}
        login = dict(LOGIN_BASE_PARAMS)
        login["userName"] = self.username
        login["password"] = hash_password(
            self.username, self._password, challenge["random"], challenge["realm"]
        )
        second = await self.request("global.login", login, check=False)
        if second.get("error") or not second.get("result"):
            err = second.get("error") or {}
            raise DahuaAuthError(f"Login rejected: {err.get('code')} {err.get('message')}")
        _LOGGER.debug("Logged in to %s, session %s", self.host, self.session_id)
        return second.get("params") or {}

    async def attach(self) -> None:
        """Subscribe to events and CommGlobal changes (SPEC §3.2)."""
        await self.request("eventManager.attach", {"codes": ["All"]}, check=False)
        await self.request("configManager.attach", {"name": "CommGlobal"}, check=False)

    # --------------------------------------------------------------- requests
    def _send(self, message: dict[str, Any]) -> int:
        """Write one frame and return its request id."""
        if not self.connected:
            raise DahuaConnectionError("Not connected")
        self._request_id += 1
        message["id"] = self._request_id
        message["session"] = self.session_id
        data = json.dumps(message, separators=(",", ":"))
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(">>> %s", _mask(message))
        payload = data.encode("utf-8", "ignore")
        assert self.transport is not None
        self.transport.write(
            struct.pack(
                DHIP_HEADER,
                DHIP_PROTO,
                self.session_id,
                self._request_id,
                len(payload),
                len(payload),
            )
            + payload
        )
        return self._request_id

    async def request(
        self,
        method: str | dict[str, Any],
        params: Any = None,
        *,
        object_id: int | None = None,
        timeout: float | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        """Send a command and wait for the matching answer (SPEC §3.5).

        ``method`` may be a full message dict, as accepted by the original
        ``send_command`` service. With ``check`` an error answer raises
        :class:`DahuaCommandError`.
        """
        message: dict[str, Any] = dict(method) if isinstance(method, dict) else {"method": method}
        if params is not None:
            message["params"] = params
        if object_id is not None:
            message["object"] = object_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        request_id = self._send(message)
        self._pending[request_id] = future
        try:
            async with asyncio.timeout(timeout or self.timeout):
                response = await future
        finally:
            self._pending.pop(request_id, None)
        if check and (response.get("error") or response.get("result") is False):
            raise DahuaCommandError(str(message.get("method")), response)
        return response

    async def instance_request(
        self,
        method: str,
        instance_params: Any,
        params: Any,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """factory.instance → method → destroy (original ``send_instance_command``)."""
        service = method.partition(".")[0]
        created = await self.request(
            f"{service}.factory.instance", instance_params, timeout=timeout, check=False
        )
        object_id = created.get("result")
        if not object_id:
            raise DahuaCommandError(f"{service}.factory.instance", created)
        try:
            return await self.request(
                method, params, object_id=object_id, timeout=timeout, check=False
            )
        finally:
            with contextlib.suppress(DahuaError, TimeoutError):
                await self.request(
                    f"{service}.destroy", object_id=object_id, timeout=timeout, check=False
                )

    # ---------------------------------------------------------- notifications
    def handle_message(self, message: dict[str, Any]) -> None:
        """Route a message to its pending future or to the callbacks."""
        method = message.get("method")
        if method == "client.notifyEventStream":
            for event in (message.get("params") or {}).get("eventList") or []:
                if self._on_event:
                    self._on_event(event)
            return
        if method == "client.notifyConfigChange":
            table = (message.get("params") or {}).get("table")
            if table is not None and self._on_config_change:
                self._on_config_change(table)
            return
        future = self._pending.get(message.get("id"))  # type: ignore[arg-type]
        if future is not None and not future.done():
            future.set_result(message)
        else:
            _LOGGER.debug("Unsolicited message ignored: %s", message.get("id"))

    def handle_connection_lost(self, exc: Exception | None) -> None:
        """Fail pending requests and notify the owner once."""
        self.transport = None
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(DahuaConnectionError("Connection lost"))
        self._pending.clear()
        if not self._closed and self._on_disconnect:
            self._on_disconnect(exc)

    async def _keepalive_loop(self) -> None:
        """Keep the session alive (SPEC §3.1); close it on failure."""
        while True:
            await asyncio.sleep(self.keepalive_interval)
            try:
                await self.request(
                    "global.keepAlive",
                    {"timeout": self.keepalive_interval, "action": True},
                    check=False,
                )
                _LOGGER.debug("Keep-alive sent to %s", self.host)
            except (DahuaError, TimeoutError) as err:
                _LOGGER.debug("Keep-alive failed on %s: %s", self.host, err)
                if self.transport is not None:
                    self.transport.close()
                return

    # ------------------------------------------------------------------ close
    def close(self) -> None:
        """Close the session without triggering the disconnect callback."""
        self._closed = True
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        if self.transport is not None:
            self.transport.close()
            self.transport = None


def _mask(message: dict[str, Any]) -> str:
    """Return the message as JSON with the login digest hidden (SPEC §7)."""
    params = message.get("params")
    if isinstance(params, dict) and "password" in params:
        message = {**message, "params": {**params, "password": "***"}}
    return json.dumps(message, separators=(",", ":"))

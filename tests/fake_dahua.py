"""Minimal fake Dahua device speaking the DHIP protocol over real TCP.

Used by the tests to exercise rpc.py end to end (SPEC §3).
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from custom_components.dahua_vto_ui.rpc import (
    DHIP_HEADER,
    DHIP_HEADER_LEN,
    DHIP_PROTO,
    DHIP_REALM_CHALLENGE,
    hash_password,
)

RANDOM = "123456"
REALM = "Login to fake"
SESSION = 4242


class FakeDahua:
    """Fake VTO/VTH server."""

    def __init__(
        self,
        device_type: str = "VTO2000A",
        serial: str = "SN123",
        username: str = "admin",
        password: str = "secret",
    ) -> None:
        self.device_type = device_type
        self.serial = serial
        self.username = username
        self.password = password
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.calls: list[dict[str, Any]] = []
        self.comm_global: dict[str, Any] = {
            "AlarmEnable": False,
            "CurrentProfile": "AtHome",
            "ProfileEnable": True,
            "Profiles": [{"name": "AtHome"}, {"name": "Outdoor"}],
        }
        self.fail_methods: set[str] = set()
        self.silent_methods: set[str] = set()

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.drop()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    def drop(self) -> None:
        """Close every client connection."""
        for w in self.writers:
            w.close()
        self.writers.clear()

    # -------------------------------------------------------------- framing
    @staticmethod
    def _frame(message: dict[str, Any]) -> bytes:
        body = json.dumps(message).encode()
        return struct.pack(DHIP_HEADER, DHIP_PROTO, SESSION, 0, len(body), len(body)) + body

    def push(self, message: dict[str, Any]) -> None:
        for w in self.writers:
            w.write(self._frame(message))

    def push_event(self, code: str, data: dict[str, Any], action: str = "Pulse") -> None:
        """Send a client.notifyEventStream with one event."""
        self.push(
            {
                "method": "client.notifyEventStream",
                "params": {
                    "eventList": [{"Code": code, "Action": action, "Index": 0, "Data": data}]
                },
            }
        )

    def push_config(self, table: dict[str, Any]) -> None:
        """Send a client.notifyConfigChange."""
        self.push({"method": "client.notifyConfigChange", "params": {"table": table}})

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        try:
            while True:
                header = await reader.readexactly(DHIP_HEADER_LEN)
                _p, _s, _r, length, _l = struct.unpack(DHIP_HEADER, header)
                msg = json.loads(await reader.readexactly(length))
                self.calls.append(msg)
                reply = self._reply(msg)
                if reply is not None:
                    reply["id"] = msg["id"]
                    reply.setdefault("session", SESSION)
                    writer.write(self._frame(reply))
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if writer in self.writers:
                self.writers.remove(writer)
            writer.close()

    # ---------------------------------------------------------------- logic
    def _reply(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method in self.silent_methods:
            return None
        if method in self.fail_methods:
            return {"result": False, "error": {"code": 1, "message": "failed"}}
        if method == "global.login":
            if "userName" not in params:
                return {
                    "result": False,
                    "error": {"code": DHIP_REALM_CHALLENGE, "message": "challenge"},
                    "params": {"random": RANDOM, "realm": REALM},
                }
            expected = hash_password(self.username, self.password, RANDOM, REALM)
            if params["userName"] != self.username or params["password"] != expected:
                return {"result": False, "error": {"code": 268632085, "message": "bad password"}}
            return {"result": True, "params": {"keepAliveInterval": 60}}
        if method == "magicBox.getSystemInfo":
            return {
                "result": True,
                "params": {"deviceType": self.device_type, "serialNumber": self.serial},
            }
        if method == "magicBox.getSoftwareVersion":
            return {"result": True, "params": {"version": {"Version": "4.300.0"}}}
        if method == "configManager.getConfig":
            return {"result": True, "params": {"table": dict(self.comm_global)}}
        if method == "configManager.setConfig":
            self.comm_global = dict(params["table"])
            return {"result": True}
        if method.endswith(".factory.instance"):
            return {"result": 99}
        return {"result": True, "params": {"echo": params}}

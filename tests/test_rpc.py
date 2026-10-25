"""RPC client against the fake device (SPEC §3)."""

from __future__ import annotations

import asyncio

import pytest

from custom_components.dahua_vto_ui.rpc import (
    DahuaAuthError,
    DahuaCommandError,
    DahuaConnectionError,
    DahuaRpcClient,
)

from .fake_dahua import FakeDahua


async def test_login_and_concurrent_requests(fake_vto: FakeDahua) -> None:
    """Two-step login works and parallel requests get their own answer (§3.5)."""
    client = DahuaRpcClient("127.0.0.1", fake_vto.port, "admin", "secret", 5)
    params = await client.connect()
    assert params["keepAliveInterval"] == 60
    a, b = await asyncio.gather(
        client.request("x.one", {"n": 1}), client.request("x.two", {"n": 2})
    )
    assert a["params"]["echo"] == {"n": 1}
    assert b["params"]["echo"] == {"n": 2}
    client.close()


async def test_wrong_password(fake_vto: FakeDahua) -> None:
    """Rejected credentials raise DahuaAuthError (§2.4)."""
    client = DahuaRpcClient("127.0.0.1", fake_vto.port, "admin", "bad", 5)
    with pytest.raises(DahuaAuthError):
        await client.connect()
    assert not client.connected


async def test_cannot_connect(socket_enabled) -> None:
    """Closed port raises DahuaConnectionError."""
    client = DahuaRpcClient("127.0.0.1", 1, "admin", "x", 2)
    with pytest.raises(DahuaConnectionError):
        await client.connect()


async def test_instance_request_and_errors(fake_vto: FakeDahua) -> None:
    """factory.instance → method(object) → destroy; errors raised when checked."""
    client = DahuaRpcClient("127.0.0.1", fake_vto.port, "admin", "secret", 5)
    await client.connect()
    await client.instance_request("accessControl.openDoor", {"channel": 0}, {"DoorIndex": 0})
    methods = [c["method"] for c in fake_vto.calls]
    assert methods[-3:] == [
        "accessControl.factory.instance",
        "accessControl.openDoor",
        "accessControl.destroy",
    ]
    assert fake_vto.calls[-2]["object"] == 99
    fake_vto.fail_methods.add("magicBox.reboot")
    with pytest.raises(DahuaCommandError):
        await client.request("magicBox.reboot")
    client.close()


async def test_notifications_and_disconnect(fake_vto: FakeDahua) -> None:
    """Events and config changes reach the callbacks; drop is reported."""
    events, configs, drops = [], [], []
    client = DahuaRpcClient(
        "127.0.0.1",
        fake_vto.port,
        "admin",
        "secret",
        5,
        on_event=events.append,
        on_config_change=configs.append,
        on_disconnect=drops.append,
    )
    await client.connect()
    fake_vto.push_event("BackKeyLight", {"State": 1})
    fake_vto.push_config({"AlarmEnable": True})
    await asyncio.sleep(0.1)
    assert events[0]["Code"] == "BackKeyLight"
    assert configs[0]["AlarmEnable"] is True
    fake_vto.drop()
    await asyncio.sleep(0.1)
    assert len(drops) == 1
    assert not client.connected

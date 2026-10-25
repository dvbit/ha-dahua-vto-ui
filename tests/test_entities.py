"""Entities, services, bus events and reconnection (SPEC §3-§6, §8)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.dahua_vto_ui.const import DOMAIN
from custom_components.dahua_vto_ui.device import DahuaVtoDevice
from custom_components.dahua_vto_ui.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import make_entry, setup_entry
from .fake_dahua import FakeDahua


async def _flush(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.1)
    await hass.async_block_till_done()


def _device_id(entry) -> str:
    return entry.runtime_data.device_id


async def test_vto_entities_english_ids(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """VTO entities exist with English entity_ids under an Italian UI (§4)."""
    hass.config.language = "it"
    await setup_entry(hass, make_entry(fake_vto, "vto"))
    for eid in (
        "sensor.citofono_connection",
        "sensor.citofono_last_call",
        "event.citofono_doorbell",
        "event.citofono_access",
        "lock.citofono_door_1",
        "binary_sensor.citofono_door_status",
        "camera.citofono_camera",
        "button.citofono_reboot",
        "button.citofono_cancel_call",
    ):
        assert hass.states.get(eid) is not None, eid
    state = hass.states.get("sensor.citofono_connection")
    assert state.state == "connected"
    assert state.attributes["device_type"] == "VTO2000A"
    assert state.attributes["software_version"] == "4.300.0"
    assert hass.states.get("alarm_control_panel.citofono_alarm") is None


async def test_doorbell_rings_once(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """States 1 then 2 = one ring; 5 = answered; unknown state ignored (§4.2)."""
    await setup_entry(hass, make_entry(fake_vto, "vto"))
    seen = []

    def _track(event):
        new = event.data["new_state"]
        if new is not None and new.attributes.get("event_type"):
            seen.append(new.attributes["event_type"])

    hass.bus.async_listen("state_changed", _track)
    for state in (1, 2, 5, 0, 1, 3):
        fake_vto.push_event("BackKeyLight", {"State": state})
        await _flush(hass)
    assert seen == ["ring", "answered", "ring"]


async def test_lock_unlock_and_relock(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Unlock opens channel 0 and relocks after unlock_period (§4.2)."""
    await setup_entry(hass, make_entry(fake_vto, "vto", {"unlock_period": 2}))
    await hass.services.async_call(
        "lock", "unlock", {"entity_id": "lock.citofono_door_1"}, blocking=True
    )
    open_call = next(c for c in fake_vto.calls if c["method"] == "accessControl.factory.instance")
    assert open_call["params"] == {"channel": 0}
    assert hass.states.get("lock.citofono_door_1").state == "unlocked"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert hass.states.get("lock.citofono_door_1").state == "locked"
    # Unlock from the keypad is reflected too.
    fake_vto.push_event("BackKeyLight", {"State": 8})
    await _flush(hass)
    assert hass.states.get("lock.citofono_door_1").state == "unlocked"


async def test_door_access_last_call(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """DoorStatus, AccessControl and VideoTalkLog update their entities."""
    await setup_entry(hass, make_entry(fake_vto, "vto"))
    fake_vto.push_event("DoorStatus", {"Status": "Open"})
    fake_vto.push_event("AccessControl", {"Status": 0, "CardNo": "AB12", "Method": 1})
    fake_vto.push_event(
        "VideoTalkLog",
        {"BeginTime": 1761400000, "EndState": "Missed", "TalkTime": 0, "LocalNumber": "9901"},
    )
    await _flush(hass)
    assert hass.states.get("binary_sensor.citofono_door_status").state == "on"
    access = hass.states.get("event.citofono_access")
    assert access.attributes["event_type"] == "denied"
    assert access.attributes["card_no"] == "AB12"
    last = hass.states.get("sensor.citofono_last_call")
    assert last.state.startswith("2025-10-25")
    assert last.attributes["missed"] is True


async def test_bus_events(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Device events are fired as dahua_vto_ui with device ids (§6)."""
    entry = make_entry(fake_vto, "vto")
    await setup_entry(hass, entry)
    events = async_capture_events(hass, DOMAIN)
    fake_vto.push_event("Invite", {"CallID": 1})
    await _flush(hass)
    assert events[0].data["Code"] == "Invite"
    assert events[0].data["device_id"] == _device_id(entry)
    assert events[0].data["entity_id"] == "sensor.citofono_connection"


async def test_services(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """send_command returns the answer and fires the event; bad target rejected (§5)."""
    entry = make_entry(fake_vto, "vto")
    await setup_entry(hass, entry)
    device_id = _device_id(entry)
    assert dr.async_get(hass).async_get(device_id).serial_number == "SN123"
    events = async_capture_events(hass, DOMAIN)
    response = await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"device_id": device_id, "method": "magicBox.getSystemInfo", "tag": "t1"},
        blocking=True,
        return_response=True,
    )
    assert response["params"]["serialNumber"] == "SN123"
    assert events[-1].data["method"] == "magicBox.getSystemInfo"
    assert events[-1].data["tag"] == "t1"

    response = await hass.services.async_call(
        DOMAIN,
        "send_instance_command",
        {
            "device_id": device_id,
            "method": "RecordUpdater.clear",
            "instance_params": {"name": "VideoTalkMissedLog"},
            "event": False,
        },
        blocking=True,
        return_response=True,
    )
    assert response["result"] is True

    await hass.services.async_call(
        DOMAIN, "open_door", {"device_id": device_id, "channel": 2}, blocking=True
    )
    assert any(c.get("params") == {"channel": 1} for c in fake_vto.calls)

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "open_door", {"device_id": "nope"}, blocking=True)


async def test_buttons(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Reboot and cancel call send their commands (§4.1, §4.2)."""
    await setup_entry(hass, make_entry(fake_vto, "vto"))
    for eid in ("button.citofono_reboot", "button.citofono_cancel_call"):
        await hass.services.async_call("button", "press", {"entity_id": eid}, blocking=True)
    methods = [c["method"] for c in fake_vto.calls]
    assert "magicBox.reboot" in methods
    assert any(c.get("params") == {"command": "hc"} for c in fake_vto.calls)


async def test_vth_alarm(hass: HomeAssistant, fake_vth: FakeDahua) -> None:
    """VTH alarm reads, writes the full table and follows changes (§4.3)."""
    await setup_entry(hass, make_entry(fake_vth, "vth"))
    assert hass.states.get("lock.monitor_door_1") is None
    alarm = "alarm_control_panel.monitor_alarm"
    assert hass.states.get(alarm).state == "disarmed"
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": alarm}, blocking=True
    )
    assert fake_vth.comm_global["AlarmEnable"] is True
    assert fake_vth.comm_global["CurrentProfile"] == "Outdoor"
    assert fake_vth.comm_global["Profiles"]  # table written back complete
    assert hass.states.get(alarm).state == "armed_away"
    fake_vth.push_config({**fake_vth.comm_global, "CurrentProfile": "Sleeping"})
    await _flush(hass)
    assert hass.states.get(alarm).state == "armed_night"


async def test_reconnect(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Drop → disconnected/unavailable, then reconnect (§3.3)."""
    with patch.object(DahuaVtoDevice, "timeout", property(lambda self: 0.2)):
        await setup_entry(hass, make_entry(fake_vto, "vto"))
        fake_vto.drop()
        await _flush(hass)
        assert hass.states.get("sensor.citofono_connection").state == "disconnected"
        assert hass.states.get("lock.citofono_door_1").state == "unavailable"
        await asyncio.sleep(0.6)
        await hass.async_block_till_done()
        assert hass.states.get("sensor.citofono_connection").state == "connected"
        assert hass.states.get("lock.citofono_door_1").state == "locked"


async def test_setup_not_ready_and_unload(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Unreachable device → retry; unload closes the session (§3.4)."""
    entry = make_entry(fake_vto, "vto")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, "port": 1})
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state.name == "SETUP_RETRY"

    hass.config_entries.async_update_entry(entry, data={**entry.data, "port": fake_vto.port})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state.name == "LOADED"
    device = entry.runtime_data
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not device.connected
    assert device.client is None


async def test_camera_and_diagnostics(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """RTSP URL built from options; diagnostics redact secrets (§4.2, §8)."""
    entry = make_entry(fake_vto, "vto", {"stream": "sub", "rtsp_port": 8554})
    await setup_entry(hass, entry)
    cam = hass.data["camera"].get_entity("camera.citofono_camera")
    url = await cam.stream_source()
    assert url == ("rtsp://admin:secret@127.0.0.1:8554/cam/realmonitor?channel=1&subtype=1")
    fake_vto.push_event("BackKeyLight", {"State": 1})
    await _flush(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["system_info"]["serial_number"] == "**REDACTED**"
    assert diag["recent_events"][-1]["Code"] == "BackKeyLight"


async def test_fingerprint_person_and_history(
    hass: HomeAssistant, fake_vto: FakeDahua, hass_storage
) -> None:
    """FingerPrintCheck → fingerprint_id + person; history persisted and served (§12.2-12.4)."""
    entry = make_entry(fake_vto, "vto", {"fingerprint_persons": {"1": "person.nicola"}})
    await setup_entry(hass, entry)
    fake_vto.push_event("BackKeyLight", {"State": 1})
    fake_vto.push_event("BackKeyLight", {"State": 2})
    fake_vto.push_event("CallSnap", {"RemoteID": "9901"})
    fake_vto.push_event("FingerPrintCheck", {"FingerPrintID": 1})
    fake_vto.push_event("AccessControl", {"Status": 1, "Method": 6, "UserID": "9901"})
    fake_vto.push_event("AccessControl", {"Status": 1, "Method": 4, "UserID": "HA"})
    await _flush(hass)

    access = hass.states.get("event.citofono_access")
    assert access.attributes["fingerprint_id"] is None  # last one was remote
    log = hass.states.get("sensor.citofono_access_log")
    history = log.attributes["history"]
    assert [h["type"] for h in history] == ["access", "access", "call"]
    assert history[1]["fingerprint_id"] == 1
    assert history[1]["person"] == "person.nicola"
    assert history[1]["method_name"] == "fingerprint"
    assert history[2]["event"] == "ring"
    assert history[2]["number"] == "9901"

    response = await hass.services.async_call(
        DOMAIN,
        "get_access_log",
        {"device_id": _device_id(entry), "limit": 1},
        blocking=True,
        return_response=True,
    )
    assert response["entries"][0]["method_name"] == "remote"

    # Persisted: unload flushes the store, reload restores it.
    assert await hass.config_entries.async_unload(entry.entry_id)
    stored = hass_storage[f"{DOMAIN}.history.{entry.entry_id}"]["data"]
    assert len(stored["entries"]) == 3
    assert stored["seen_fingerprints"] == [1]
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.history.entries) == 3


async def test_fingerprint_too_old_ignored(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """A FingerPrintCheck older than 10 s is not paired (§12.2)."""
    entry = make_entry(fake_vto, "vto")
    await setup_entry(hass, entry)
    fake_vto.push_event("FingerPrintCheck", {"FingerPrintID": 2})
    await _flush(hass)
    with patch("custom_components.dahua_vto_ui.history.monotonic", return_value=1e12):
        fake_vto.push_event("AccessControl", {"Status": 1, "Method": 6, "UserID": "9901"})
        await _flush(hass)
    assert hass.states.get("event.citofono_access").attributes["fingerprint_id"] is None


async def test_vth_disarm_pin(hass: HomeAssistant, fake_vth: FakeDahua) -> None:
    """PIN only for disarm; wrong PIN refused without commands (§12.1)."""
    fake_vth.comm_global["AlarmEnable"] = True
    entry = make_entry(fake_vth, "vth", {"disarm_code": "1234"})
    await setup_entry(hass, entry)
    alarm = "alarm_control_panel.monitor_alarm"
    state = hass.states.get(alarm)
    assert state.attributes["code_format"] == "number"
    assert state.attributes["code_arm_required"] is False
    calls = len(fake_vth.calls)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "alarm_control_panel",
            "alarm_disarm",
            {"entity_id": alarm, "code": "0000"},
            blocking=True,
        )
    assert len(fake_vth.calls) == calls
    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": alarm, "code": "1234"}, blocking=True
    )
    assert fake_vth.comm_global["AlarmEnable"] is False
    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_home", {"entity_id": alarm}, blocking=True
    )
    assert fake_vth.comm_global["AlarmEnable"] is True

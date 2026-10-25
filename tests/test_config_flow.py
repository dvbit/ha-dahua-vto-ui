"""Config, reauth and options flows (SPEC §2)."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.dahua_vto_ui.const import CONF_DEVICE_KIND, DOMAIN

from .conftest import make_entry, setup_entry
from .fake_dahua import FakeDahua


def _input(dev: FakeDahua, password: str = "secret", name: str = "") -> dict:
    return {
        "name": name,
        CONF_HOST: "127.0.0.1",
        CONF_PORT: dev.port,
        CONF_USERNAME: "admin",
        CONF_PASSWORD: password,
    }


async def test_user_flow_vto(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Valid login creates the entry; type and serial detected (§2.1-2.3)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _input(fake_vto))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "VTO2000A"
    assert result["data"][CONF_DEVICE_KIND] == "vto"
    assert result["result"].unique_id == "SN123"
    assert "name" not in result["data"]
    await hass.async_block_till_done()


async def test_user_flow_vth_named(hass: HomeAssistant, fake_vth: FakeDahua) -> None:
    """VTH model detected; custom name used as title."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _input(fake_vth, name="Monitor cucina")
    )
    assert result["title"] == "Monitor cucina"
    assert result["data"][CONF_DEVICE_KIND] == "vth"
    await hass.async_block_till_done()


async def test_user_flow_errors(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Wrong password and unreachable host show form errors (§2.1)."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _input(fake_vto, password="bad")
    )
    assert result["errors"] == {"base": "invalid_auth"}
    bad = _input(fake_vto)
    bad[CONF_PORT] = 1
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_already_configured_updates_host(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Same serial: host/port updated, flow aborted (§2.2)."""
    entry = make_entry(fake_vto, "vto")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PORT: 9})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _input(fake_vto))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PORT] == fake_vto.port


async def test_reauth(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Reauth stores the new password and reloads (§2.4)."""
    entry = make_entry(fake_vto, "vto")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PASSWORD: "old"})
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: "admin", CONF_PASSWORD: "secret"}
    )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "secret"
    await hass.async_block_till_done()


async def test_options_vto_and_vth(
    hass: HomeAssistant, fake_vto: FakeDahua, fake_vth: FakeDahua
) -> None:
    """VTO menu → settings keeps other options; VTH settings has the PIN (§2.5, §12.1)."""
    vto = make_entry(fake_vto, "vto", {"fingerprint_persons": {"1": "person.nicola"}})
    await setup_entry(hass, vto)
    result = await hass.config_entries.options.async_init(vto.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["settings", "fingerprints"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    keys = {str(k) for k in result["data_schema"].schema}
    assert {"timeout", "lock_channels", "unlock_period", "short_number", "stream"} <= keys
    assert "disarm_code" not in keys
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "timeout": 7,
            "lock_channels": 2,
            "unlock_period": 3,
            "short_number": "HA",
            "rtsp_port": 554,
            "http_port": 80,
            "stream": "sub",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert vto.options["lock_channels"] == 2
    assert vto.options["fingerprint_persons"] == {"1": "person.nicola"}  # kept
    assert hass.states.get("lock.citofono_door_2") is not None  # reloaded

    vth = make_entry(fake_vth, "vth")
    await setup_entry(hass, vth)
    result = await hass.config_entries.options.async_init(vth.entry_id)
    assert result["step_id"] == "settings"
    assert {str(k) for k in result["data_schema"].schema} == {"timeout", "disarm_code"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"timeout": 10, "disarm_code": " 1234 "}
    )
    assert vth.options["disarm_code"] == "1234"


async def test_options_fingerprints(hass: HomeAssistant, fake_vto: FakeDahua) -> None:
    """Seen IDs offered; add, validate and remove a link (§12.3)."""
    entry = make_entry(fake_vto, "vto")
    await setup_entry(hass, entry)
    entry.runtime_data.history.seen_fingerprints.add(3)

    async def _open():
        result = await hass.config_entries.options.async_init(entry.entry_id)
        return await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "fingerprints"}
        )

    result = await _open()
    assert result["step_id"] == "fingerprints"
    selector = result["data_schema"].schema["fingerprint_id"]
    assert selector.config["options"] == ["3"]
    bad = await hass.config_entries.options.async_configure(
        result["flow_id"], {"fingerprint_id": "abc"}
    )
    assert bad["errors"] == {"fingerprint_id": "invalid_fingerprint"}
    done = await hass.config_entries.options.async_configure(
        result["flow_id"], {"fingerprint_id": "3", "person": "person.nicola"}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["fingerprint_persons"] == {"3": "person.nicola"}
    assert entry.options["timeout"] == 5  # other options kept

    result = await _open()
    await hass.config_entries.options.async_configure(result["flow_id"], {"fingerprint_id": "3"})
    await hass.async_block_till_done()
    assert entry.options["fingerprint_persons"] == {}

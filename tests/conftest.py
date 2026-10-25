"""Fixtures: fake device and config entries."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dahua_vto_ui.const import (
    CONF_DEVICE_KIND,
    CONF_MODEL,
    CONF_SERIAL,
    DOMAIN,
)

from .fake_dahua import FakeDahua


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return


@pytest.fixture
async def fake_vto(socket_enabled) -> AsyncGenerator[FakeDahua]:
    """Running fake VTO."""
    dev = FakeDahua()
    await dev.start()
    yield dev
    await dev.stop()


@pytest.fixture
async def fake_vth(socket_enabled) -> AsyncGenerator[FakeDahua]:
    """Running fake VTH."""
    dev = FakeDahua(device_type="VTH5221DW", serial="SNVTH")
    await dev.start()
    yield dev
    await dev.stop()


def make_entry(dev: FakeDahua, kind: str, options: dict | None = None) -> MockConfigEntry:
    """Config entry pointing at a fake device."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Citofono" if kind == "vto" else "Monitor",
        unique_id=dev.serial,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: dev.port,
            CONF_USERNAME: dev.username,
            CONF_PASSWORD: dev.password,
            CONF_DEVICE_KIND: kind,
            CONF_MODEL: dev.device_type,
            CONF_SERIAL: dev.serial,
        },
        options={"timeout": 5, **(options or {})},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up an entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

"""VTO camera: RTSP stream and HTTP snapshot (SPEC §4.2, VTO only)."""

from __future__ import annotations

import logging
from urllib.parse import quote

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.httpx_client import get_async_client
import httpx

from . import DahuaConfigEntry
from .const import (
    CONF_HTTP_PORT,
    CONF_RTSP_PORT,
    CONF_STREAM,
    DEFAULT_HTTP_PORT,
    DEFAULT_RTSP_PORT,
    DEFAULT_STREAM,
    STREAM_SUB,
)
from .device import DahuaVtoDevice
from .entity import DahuaVtoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DahuaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the VTO camera."""
    async_add_entities([DahuaVtoCamera(entry.runtime_data)])


class DahuaVtoCamera(DahuaVtoEntity, Camera):
    """Camera built on the standard Dahua URLs."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, device: DahuaVtoDevice) -> None:
        """Create the camera."""
        DahuaVtoEntity.__init__(self, device, "camera", "camera")
        Camera.__init__(self)
        self._snapshot_failed = False

    @property
    def _opts(self) -> dict:
        return {**self.device.entry.data, **self.device.entry.options}

    async def stream_source(self) -> str | None:
        """RTSP URL ``cam/realmonitor`` (subtype 0 = main, 1 = sub)."""
        o = self._opts
        subtype = 1 if o.get(CONF_STREAM, DEFAULT_STREAM) == STREAM_SUB else 0
        user = quote(o[CONF_USERNAME], safe="")
        pwd = quote(o[CONF_PASSWORD], safe="")
        return (
            f"rtsp://{user}:{pwd}@{o[CONF_HOST]}:{o.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT)}"
            f"/cam/realmonitor?channel=1&subtype={subtype}"
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """JPEG from ``cgi-bin/snapshot.cgi`` with digest auth."""
        o = self._opts
        url = (
            f"http://{o[CONF_HOST]}:{o.get(CONF_HTTP_PORT, DEFAULT_HTTP_PORT)}"
            "/cgi-bin/snapshot.cgi?channel=1"
        )
        client = get_async_client(self.hass, verify_ssl=False)
        try:
            response = await client.get(
                url,
                auth=httpx.DigestAuth(o[CONF_USERNAME], o[CONF_PASSWORD]),
                timeout=self.device.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as err:
            if not self._snapshot_failed:
                _LOGGER.warning("%s: snapshot not available (%s)", self.device.name, err)
                self._snapshot_failed = True
            return None
        if self._snapshot_failed:
            _LOGGER.info("%s: snapshot available again", self.device.name)
            self._snapshot_failed = False
        _LOGGER.debug("%s: snapshot %d bytes", self.device.name, len(response.content))
        return response.content

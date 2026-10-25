"""Persistent access and call history (SPEC §12.2-12.4).

Device events are turned into compact history entries here, so the doorbell
event entity, the access event entity, the access log sensor and the
``get_access_log`` service all share the same interpretation:

- ``BackKeyLight`` → call entries (ring once per call, answered, missed,
  voice message), with the number from ``CallSnap`` and the outcome from
  ``VideoTalkLog`` merged into the latest call when they arrive close by;
- ``FingerPrintCheck`` → remembered for 10 s and attached as
  ``fingerprint_id`` to the next fingerprint ``AccessControl`` (§12.2);
- ``AccessControl`` → access entries with method, result, user and the
  person mapped to the fingerprint in the options (§12.3).

The last 60 entries and the fingerprint IDs ever seen are saved with HA's
``Store`` so they survive restarts (§12.4).
"""

from __future__ import annotations

from collections import deque
import logging
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ACCESS_AUTHORIZED,
    ACCESS_DENIED,
    BKL_ANSWERED,
    BKL_MISSED,
    BKL_RING,
    BKL_VOICE_MESSAGE,
    DOMAIN,
    DOORBELL_ANSWERED,
    DOORBELL_MISSED,
    DOORBELL_RING,
    DOORBELL_VOICE_MESSAGE,
    HISTORY_SIZE,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY = 5  # seconds, batches bursts of events into one write

# Max age of a FingerPrintCheck to be paired with AccessControl (§12.2).
FINGERPRINT_WINDOW = 10.0
# Max age of the latest call entry to receive CallSnap / VideoTalkLog details.
CALL_MERGE_WINDOW = 120.0

# AccessControl "Method" codes (original README, DahuaVTO2MQTT notes).
METHOD_FINGERPRINT = 6
METHOD_NAMES: dict[int, str] = {1: "card", 4: "remote", 6: "fingerprint"}

# BackKeyLight state → call event (README table, SPEC §4.2).
BKL_CALL_EVENTS: dict[int, str] = {
    BKL_VOICE_MESSAGE: DOORBELL_VOICE_MESSAGE,
    BKL_ANSWERED: DOORBELL_ANSWERED,
    BKL_MISSED: DOORBELL_MISSED,
}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class AccessHistory:
    """Interpret events, keep the last entries and persist them."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Create the store for one config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.history.{entry_id}"
        )
        self.entries: deque[dict[str, Any]] = deque(maxlen=HISTORY_SIZE)
        self.seen_fingerprints: set[int] = set()
        self._last_bkl: int | None = None
        self._last_fp: tuple[int, float] | None = None
        self._last_call_at: float = 0.0

    # ------------------------------------------------------------- storage
    async def async_load(self) -> None:
        """Restore entries and seen fingerprints (§12.4)."""
        data = await self._store.async_load() or {}
        self.entries.extend(reversed(data.get("entries", [])))
        self.seen_fingerprints = {int(x) for x in data.get("seen_fingerprints", [])}
        _LOGGER.debug(
            "History restored: %d entries, fingerprints %s",
            len(self.entries),
            sorted(self.seen_fingerprints),
        )

    def _data(self) -> dict[str, Any]:
        return {
            "entries": self.newest_first(),
            "seen_fingerprints": sorted(self.seen_fingerprints),
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write pending changes now (unload)."""
        await self._store.async_save(self._data())

    async def async_remove(self) -> None:
        """Delete the file when the entry is removed."""
        await self._store.async_remove()

    def newest_first(self, limit: int | None = None) -> list[dict[str, Any]]:
        """Entries from the most recent (§12.4)."""
        items = list(reversed(self.entries))
        return items[:limit] if limit else items

    # ------------------------------------------------------- interpretation
    def _add(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry = {"time": dt_util.utcnow().isoformat(), **entry}
        self.entries.append(entry)
        self._schedule_save()
        return entry

    def handle_back_key_light(self, data: dict[str, Any]) -> str | None:
        """Return the doorbell event type for a BackKeyLight state, if any.

        ``ring`` is returned once per call even if states 1 and 2 follow
        each other (SPEC §4.2). A call entry is added to the history.
        """
        state = _int(data.get("State"))
        if state is None:
            return None
        previous, self._last_bkl = self._last_bkl, state
        if state in BKL_RING:
            if previous in BKL_RING:
                return None
            event_type = DOORBELL_RING
        elif (event_type := BKL_CALL_EVENTS.get(state)) is None:
            return None
        self._add({"type": "call", "event": event_type, "number": None})
        self._last_call_at = monotonic()
        return event_type

    def _latest_call(self) -> dict[str, Any] | None:
        if monotonic() - self._last_call_at > CALL_MERGE_WINDOW:
            return None
        for entry in reversed(self.entries):
            if entry.get("type") == "call":
                return entry
        return None

    def handle_call_snap(self, data: dict[str, Any]) -> None:
        """Attach the called number to the latest call (§12.4)."""
        if (call := self._latest_call()) is not None and data.get("RemoteID"):
            call["number"] = data.get("RemoteID")
            self._schedule_save()

    def handle_video_talk_log(self, data: dict[str, Any]) -> None:
        """Attach talk time and outcome to the latest call (§12.4)."""
        if (call := self._latest_call()) is not None:
            call["talk_time"] = data.get("TalkTime")
            call["end_state"] = data.get("EndState")
            if data.get("LocalNumber") and not call.get("number"):
                call["number"] = data.get("LocalNumber")
            self._schedule_save()

    def handle_fingerprint_check(self, data: dict[str, Any]) -> None:
        """Remember the fingerprint for the next AccessControl (§12.2)."""
        if (fp := _int(data.get("FingerPrintID"))) is None:
            return
        self._last_fp = (fp, monotonic())
        if fp not in self.seen_fingerprints:
            self.seen_fingerprints.add(fp)
            _LOGGER.info("New fingerprint ID seen: %s", fp)
            self._schedule_save()

    def handle_access_control(
        self, data: dict[str, Any], persons: dict[str, str]
    ) -> dict[str, Any]:
        """Build and store an access entry (§12.2-12.4); return its attributes."""
        method = _int(data.get("Method"))
        fingerprint_id: int | None = None
        if method == METHOD_FINGERPRINT and self._last_fp is not None:
            fp, seen_at = self._last_fp
            if monotonic() - seen_at <= FINGERPRINT_WINDOW:
                fingerprint_id = fp
            self._last_fp = None
        entry = self._add(
            {
                "type": "access",
                "result": ACCESS_AUTHORIZED if str(data.get("Status")) == "1" else ACCESS_DENIED,
                "method": method,
                "method_name": METHOD_NAMES.get(method or -1, "other"),
                "user_id": data.get("UserID"),
                "card_no": data.get("CardNo"),
                "fingerprint_id": fingerprint_id,
                "person": persons.get(str(fingerprint_id)) if fingerprint_id is not None else None,
            }
        )
        return entry

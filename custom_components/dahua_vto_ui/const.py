"""Constants for the Dahua VTO UI integration (see SPEC.md)."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dahua_vto_ui"

# Bus event fired for every device notification and command result (SPEC §6).
EVENT_BUS: Final = DOMAIN

# --- Config entry data (SPEC §2.1) -----------------------------------------
CONF_DEVICE_KIND: Final = "device_kind"
CONF_SERIAL: Final = "serial_number"
CONF_MODEL: Final = "model"

DEFAULT_PORT: Final = 5000
DEFAULT_USERNAME: Final = "admin"

# Device kinds (SPEC §2.3).
KIND_VTO: Final = "vto"
KIND_VTH: Final = "vth"

# --- Options (SPEC §2.5) ---------------------------------------------------
CONF_LOCK_CHANNELS: Final = "lock_channels"
CONF_UNLOCK_PERIOD: Final = "unlock_period"
CONF_SHORT_NUMBER: Final = "short_number"
CONF_RTSP_PORT: Final = "rtsp_port"
CONF_HTTP_PORT: Final = "http_port"
CONF_STREAM: Final = "stream"

DEFAULT_TIMEOUT: Final = 10
DEFAULT_LOCK_CHANNELS: Final = 1
DEFAULT_UNLOCK_PERIOD: Final = 2
DEFAULT_SHORT_NUMBER: Final = "HA"
DEFAULT_RTSP_PORT: Final = 554
DEFAULT_HTTP_PORT: Final = 80
STREAM_MAIN: Final = "main"
STREAM_SUB: Final = "sub"
DEFAULT_STREAM: Final = STREAM_MAIN

MIN_TIMEOUT: Final = 5
MAX_TIMEOUT: Final = 60
MAX_LOCK_CHANNELS: Final = 2
MAX_UNLOCK_PERIOD: Final = 30

# --- Services (SPEC §5) ----------------------------------------------------
SERVICE_OPEN_DOOR: Final = "open_door"
SERVICE_SEND_COMMAND: Final = "send_command"
SERVICE_SEND_INSTANCE_COMMAND: Final = "send_instance_command"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_CHANNEL: Final = "channel"
ATTR_METHOD: Final = "method"
ATTR_PARAMS: Final = "params"
ATTR_INSTANCE_PARAMS: Final = "instance_params"
ATTR_EVENT: Final = "event"
ATTR_TAG: Final = "tag"
ATTR_TIMEOUT: Final = "timeout"

SERVICE_DEFAULT_TIMEOUT: Final = 5

# --- Device event codes (README of the original integration) --------------
CODE_BACK_KEY_LIGHT: Final = "BackKeyLight"
CODE_ACCESS_CONTROL: Final = "AccessControl"
CODE_DOOR_STATUS: Final = "DoorStatus"
CODE_VIDEO_TALK_LOG: Final = "VideoTalkLog"
CODE_CONFIG_CHANGE: Final = "client.notifyConfigChange"
CODE_FINGERPRINT_CHECK: Final = "FingerPrintCheck"
CODE_CALL_SNAP: Final = "CallSnap"

# BackKeyLight states (README table, SPEC §4.2).
BKL_IDLE: Final = 0
BKL_RING: Final = (1, 2)
BKL_VOICE_MESSAGE: Final = 4
BKL_ANSWERED: Final = 5
BKL_MISSED: Final = 6
BKL_VTH_CALLING: Final = 7
BKL_UNLOCK: Final = 8
BKL_UNLOCK_FAILED: Final = 9
BKL_REBOOT: Final = 11
BKL_KNOWN: Final = frozenset({0, 1, 2, 4, 5, 6, 7, 8, 9, 11})

# Doorbell event types (SPEC §4.2).
DOORBELL_RING: Final = "ring"
DOORBELL_VOICE_MESSAGE: Final = "voice_message"
DOORBELL_ANSWERED: Final = "answered"
DOORBELL_MISSED: Final = "missed"
DOORBELL_EVENT_TYPES: Final = [
    DOORBELL_RING,
    DOORBELL_VOICE_MESSAGE,
    DOORBELL_ANSWERED,
    DOORBELL_MISSED,
]

# Access event types (SPEC §4.2).
ACCESS_AUTHORIZED: Final = "authorized"
ACCESS_DENIED: Final = "denied"
ACCESS_EVENT_TYPES: Final = [ACCESS_AUTHORIZED, ACCESS_DENIED]

# VTH alarm (SPEC §4.3).
CONFIG_COMM_GLOBAL: Final = "CommGlobal"
PROFILE_AT_HOME: Final = "AtHome"
PROFILE_OUTDOOR: Final = "Outdoor"
PROFILE_SLEEPING: Final = "Sleeping"
PROFILE_CUSTOM: Final = "Custom"

# Connection sensor states (SPEC §4.1).
STATE_CONNECTED: Final = "connected"
STATE_DISCONNECTED: Final = "disconnected"

# --- v2.1.0 (SPEC §12) -----------------------------------------------------
CONF_DISARM_CODE: Final = "disarm_code"  # §12.1
CONF_FINGERPRINT_PERSONS: Final = "fingerprint_persons"  # §12.3, {"<id>": "person.x"}
CONF_FINGERPRINT_ID: Final = "fingerprint_id"
CONF_PERSON: Final = "person"
HISTORY_SIZE: Final = 60  # §12.4
SERVICE_GET_ACCESS_LOG: Final = "get_access_log"
ATTR_LIMIT: Final = "limit"

# Number of recent events kept for diagnostics (SPEC §8).
EVENT_HISTORY_SIZE: Final = 20

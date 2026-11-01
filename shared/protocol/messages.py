"""AIRA device protocol — the contract shared by the laptop server and every device.

Every message on the WebSocket link is one JSON object: an :class:`Envelope`.
The envelope is versioned so that additional physical devices (a second Pi,
an ESP32 status light, a phone) can join later without breaking existing ones.

The laptop serves it at ``wss://<laptop>:8765/api/v1/device/ws`` (laptop_server/api/
device_routes.py); the Raspberry Pi client is raspberry_pi/aira_pi/ (which keeps its own
dependency-free copy of the names below, checked by tests/unit/test_device_link.py).

1.1 (additive): USER_TEXT, CAPTION, SAY, REMINDER, CONFIRM_REQUEST; DisplayState.hold_s.
1.2 (additive): SHOW_SCREEN; CAPTION.source.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1.2"


class MessageType(str, Enum):
    """All message types understood by protocol version 1.0."""

    # Session / link management
    DEVICE_HELLO = "DEVICE_HELLO"            # device -> server, first frame after connect
    SERVER_WELCOME = "SERVER_WELCOME"        # server -> device, auth accepted
    DEVICE_HEARTBEAT = "DEVICE_HEARTBEAT"    # both directions, keep-alive

    # Voice pipeline
    WAKE_WORD_DETECTED = "WAKE_WORD_DETECTED"
    LISTENING_STARTED = "LISTENING_STARTED"
    LISTENING_STOPPED = "LISTENING_STOPPED"
    AUDIO_CHUNK = "AUDIO_CHUNK"              # base64 PCM16 mono 16 kHz in payload
    TRANSCRIPTION_READY = "TRANSCRIPTION_READY"
    THINKING_STARTED = "THINKING_STARTED"
    SPEECH_STARTED = "SPEECH_STARTED"
    SPEECH_FINISHED = "SPEECH_FINISHED"
    BARGE_IN = "BARGE_IN"                    # user interrupted AIRA while she was speaking

    # Display / status
    DISPLAY_STATE_CHANGED = "DISPLAY_STATE_CHANGED"
    SYSTEM_STATUS = "SYSTEM_STATUS"
    BUTTON_EVENT = "BUTTON_EVENT"
    TOUCH_EVENT = "TOUCH_EVENT"

    # Conversation mirroring and device speech (1.1)
    USER_TEXT = "USER_TEXT"                  # device -> server: something the user said/typed on the device
    CAPTION = "CAPTION"                      # server -> device: {role: user|aira, text}
    SAY = "SAY"                              # server -> device with audio: speak this sentence
    REMINDER = "REMINDER"                    # server -> device: a timer or reminder went off
    CONFIRM_REQUEST = "CONFIRM_REQUEST"      # server -> device: AIRA is waiting for yes/no
    SHOW_SCREEN = "SHOW_SCREEN"              # server -> device (1.2): {screen: home|chat|music|tools|system}

    ERROR = "ERROR"


class Expression(str, Enum):
    """Facial expressions the Pi face engine must be able to render."""

    IDLE = "idle"
    BLINKING = "blinking"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    HAPPY = "happy"
    EXCITED = "excited"
    CONFUSED = "confused"
    SLEEP = "sleep"
    ERROR = "error"


class Envelope(BaseModel):
    """One protocol frame. ``payload`` is type-specific and validated by the receiver."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    v: str = Field(default=PROTOCOL_VERSION, description="Protocol version")
    type: MessageType
    id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=64)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    device_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    reply_to: str | None = Field(default=None, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Envelope":
        return cls.model_validate_json(raw)


# ---- Typed payloads for the messages whose shape matters most -------------------


class DisplayStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: Expression
    intensity: float = Field(default=1.0, ge=0.0, le=1.0)
    caption: str | None = Field(default=None, max_length=120)
    hold_s: float | None = Field(default=None, ge=0, le=60,
                                 description="Return to idle after this long unless another state arrives")


class ErrorPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(max_length=64)
    message: str = Field(max_length=500)
    recoverable: bool = True


class HeartbeatPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uptime_s: float = Field(ge=0)
    cpu_temp_c: float | None = None
    wifi_rssi_dbm: int | None = None

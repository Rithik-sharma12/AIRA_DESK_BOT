"""AIRA device protocol — the contract shared by the laptop server and every device.

Every message on the WebSocket link is one JSON object: an :class:`Envelope`.
The envelope is versioned so that additional physical devices (a second Pi,
an ESP32 status light, a phone) can join later without breaking existing ones.

Phase 1 only *defines* this contract and tests it; the WebSocket transport that
carries it is implemented in Phase 6.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROTOCOL_VERSION = "1.0"


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

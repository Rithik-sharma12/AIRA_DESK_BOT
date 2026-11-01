"""Dependency-free copy of the AIRA device protocol (shared/protocol/messages.py).

The laptop validates frames with pydantic; the Pi only needs the names and the envelope
shape. tests/unit/test_device_link.py checks that this copy matches the original.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

PROTOCOL_VERSION = "1.1"

MESSAGE_TYPES = (
    "DEVICE_HELLO", "SERVER_WELCOME", "DEVICE_HEARTBEAT",
    "WAKE_WORD_DETECTED", "LISTENING_STARTED", "LISTENING_STOPPED", "AUDIO_CHUNK", "TRANSCRIPTION_READY",
    "THINKING_STARTED", "SPEECH_STARTED", "SPEECH_FINISHED", "BARGE_IN",
    "DISPLAY_STATE_CHANGED", "SYSTEM_STATUS", "BUTTON_EVENT", "TOUCH_EVENT",
    "USER_TEXT", "CAPTION", "SAY", "REMINDER", "CONFIRM_REQUEST",
    "ERROR",
)

EXPRESSIONS = ("idle", "blinking", "listening", "thinking", "speaking", "happy", "excited", "confused",
               "sleep", "error")


def envelope(msg_type: str, device_id: str, payload: dict[str, Any] | None = None,
             session_id: str | None = None) -> str:
    if msg_type not in MESSAGE_TYPES:
        raise ValueError(f"unknown message type {msg_type}")
    frame = {"v": PROTOCOL_VERSION, "type": msg_type, "id": uuid.uuid4().hex,
             "ts": datetime.now(timezone.utc).isoformat(), "device_id": device_id,
             "session_id": session_id, "reply_to": None, "payload": payload or {}}
    return json.dumps(frame, ensure_ascii=False, separators=(",", ":"))


def parse(raw: str | bytes) -> dict[str, Any] | None:
    try:
        frame = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(frame, dict) or frame.get("type") not in MESSAGE_TYPES:
        return None
    if not isinstance(frame.get("payload"), dict):
        frame["payload"] = {}
    return frame

import pytest
from pydantic import ValidationError

from shared.protocol import DisplayStatePayload, Envelope, Expression, MessageType


def test_round_trip():
    e = Envelope(type=MessageType.DISPLAY_STATE_CHANGED, device_id="pi-zero-01",
                 payload=DisplayStatePayload(expression=Expression.HAPPY).model_dump(mode="json"))
    back = Envelope.from_json(e.to_json())
    assert back.type is MessageType.DISPLAY_STATE_CHANGED
    assert DisplayStatePayload(**back.payload).expression is Expression.HAPPY
    assert back.v == "1.0"


def test_spec_message_types_present():
    required = {"WAKE_WORD_DETECTED", "LISTENING_STARTED", "AUDIO_CHUNK", "TRANSCRIPTION_READY",
                "THINKING_STARTED", "SPEECH_STARTED", "SPEECH_FINISHED", "DISPLAY_STATE_CHANGED",
                "SYSTEM_STATUS", "ERROR", "DEVICE_HEARTBEAT"}
    assert required <= {m.value for m in MessageType}
    assert len(Expression) == 10


def test_rejects_unknown_type_extra_fields_and_bad_ids():
    with pytest.raises(ValidationError):
        Envelope.from_json('{"type": "FORMAT_DISK", "device_id": "pi"}')
    with pytest.raises(ValidationError):
        Envelope(type=MessageType.ERROR, device_id="pi", sneaky=1)
    with pytest.raises(ValidationError):
        Envelope(type=MessageType.ERROR, device_id="pi; rm -rf /")

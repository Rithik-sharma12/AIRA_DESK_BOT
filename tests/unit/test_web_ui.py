"""Web voice interface: page, pairing, auth with web tokens, speech endpoints."""

import io
import json

from fastapi.testclient import TestClient

from laptop_server.app import create_app
from laptop_server.security.web_pairing import MAX_FAILURES
from laptop_server.voice.audio import AudioClip
from laptop_server.voice.service import VoiceService
from laptop_server.voice.stt import Transcript
from laptop_server.voice.tts import SynthesisResult, TTSProvider
from tests.conftest import FakeProvider, make_settings


class FakeSTT:
    name = "fake-stt"

    def __init__(self):
        self.calls = []

    def transcribe(self, path, language=None):
        self.calls.append((path.suffix, path.stat().st_size, language))
        return Transcript(text="vanakkam AIRA", language=language or "ta", language_probability=0.9,
                          audio_s=1.5, latency_s=0.2)


class FakeTTS(TTSProvider):
    name = "fake-tts"

    def __init__(self):
        self.texts = []

    async def synthesize(self, text, voice):
        self.texts.append(text)
        return SynthesisResult(AudioClip.silence(0.3, 16000), 0.01, 0.02)


def wav_bytes():
    return AudioClip.silence(0.5, 16000).to_wav_bytes()


def make(voice=None, **settings):
    s = make_settings(**settings)
    app = create_app(s, provider=FakeProvider(), voice=voice or VoiceService(s))
    return app, s


def pair(c, app):
    r = c.post("/api/v1/web/pair", json={"code": app.state.web_pairing.code, "name": "Redmi"})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_page_is_served():
    app, _ = make()
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and "AIRA" in r.text and "getUserMedia" in r.text


def test_web_ui_can_be_disabled():
    app, _ = make(web_ui_enabled=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 404


def test_pairing_gives_a_token_that_authenticates_and_is_stored_hashed():
    app, s = make()
    with TestClient(app) as c:
        assert c.get("/api/v1/status").status_code == 401
        assert c.post("/api/v1/web/hello").json() == {"locked": False}
        old_code = app.state.web_pairing.code
        h = pair(c, app)
        assert app.state.web_pairing.code != old_code  # one-time code
        assert c.get("/api/v1/status", headers=h).status_code == 200
        r = c.post("/api/v1/chat", json={"message": "hi"}, headers=h)
        assert r.status_code == 200 and r.json()["reply"] == "Hello from AIRA!"
        stored = (s.data_dir / "web_devices.json").read_text()
        assert h["Authorization"].split()[1] not in stored and "Redmi" in stored
    # survives a restart
    app2 = create_app(s, provider=FakeProvider())
    with TestClient(app2) as c:
        assert c.get("/api/v1/status", headers=h).status_code == 200


def test_pairing_locks_after_wrong_codes():
    app, _ = make()
    with TestClient(app) as c:
        wrong = "000000" if app.state.web_pairing.code != "000000" else "111111"
        for _ in range(MAX_FAILURES):
            assert c.post("/api/v1/web/pair", json={"code": wrong}).status_code == 403
        r = c.post("/api/v1/web/pair", json={"code": app.state.web_pairing.code})
        assert r.status_code == 403 and "locked" in r.json()["detail"]
        assert c.post("/api/v1/web/hello").json() == {"locked": True}


def test_without_engines_capabilities_are_empty_and_endpoints_say_501():
    app, _ = make()
    with TestClient(app) as c:
        h = pair(c, app)
        assert c.get("/api/v1/voice/capabilities", headers=h).json()["stt"] is None
        r = c.post("/api/v1/voice/transcribe", headers=h, files={"audio": ("u.wav", wav_bytes(), "audio/wav")})
        assert r.status_code == 501
        assert c.post("/api/v1/voice/speak", headers=h, json={"text": "hello"}).status_code == 501


def test_voice_endpoints_require_auth():
    app, _ = make()
    with TestClient(app) as c:
        assert c.get("/api/v1/voice/capabilities").status_code == 401
        assert c.post("/api/v1/voice/speak", json={"text": "x"}).status_code == 401


def test_transcribe_and_speak_with_engines():
    s = make_settings()
    stt, tts = FakeSTT(), FakeTTS()
    app = create_app(s, provider=FakeProvider(), voice=VoiceService(s, stt=stt, tts=tts))
    with TestClient(app) as c:
        h = pair(c, app)
        caps = c.get("/api/v1/voice/capabilities", headers=h).json()
        assert caps["stt"] == "fake-stt" and caps["tts"] == "fake-tts"
        r = c.post("/api/v1/voice/transcribe", headers=h, data={"language": "ta"},
                   files={"audio": ("utterance", b"\x1aE\xdf\xa3fake-webm", "audio/webm;codecs=opus")})
        assert r.status_code == 200 and r.json()["text"] == "vanakkam AIRA"
        assert stt.calls[-1][0] == ".webm" and stt.calls[-1][2] == "ta"
        c.post("/api/v1/voice/transcribe", headers=h, data={"language": "auto"},
               files={"audio": ("u.wav", wav_bytes(), "audio/wav")})
        assert stt.calls[-1][0] == ".wav" and stt.calls[-1][2] is None
        r = c.post("/api/v1/voice/speak", headers=h, json={"text": "வணக்கம்!"})
        assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
        assert AudioClip.from_wav_bytes(r.content).duration_s > 0.2 and tts.texts == ["வணக்கம்!"]


def test_upload_limits():
    s = make_settings(max_audio_upload_mb=0.01)
    app = create_app(s, provider=FakeProvider(), voice=VoiceService(s, stt=FakeSTT(), tts=FakeTTS()))
    with TestClient(app) as c:
        h = pair(c, app)
        big = c.post("/api/v1/voice/transcribe", headers=h, files={"audio": ("u.wav", b"x" * 20000, "audio/wav")})
        assert big.status_code == 413
        empty = c.post("/api/v1/voice/transcribe", headers=h, files={"audio": ("u.wav", b"", "audio/wav")})
        assert empty.status_code == 422
        assert c.post("/api/v1/voice/speak", headers=h, json={"text": "x" * 1001}).status_code == 422


def test_auto_capabilities_follow_settings(tmp_path):
    from pydantic import SecretStr

    s = make_settings(tts_provider="auto", azure_speech_key=SecretStr("k" * 32), stt_provider="none")
    v = VoiceService(s)
    assert v.tts_name == "azure" and v.stt_name is None
    s2 = make_settings(tts_provider="piper", tts_piper_model=tmp_path / "missing.onnx")
    assert VoiceService(s2).tts_name is None

"""Azure-first speech with automatic offline Piper fallback."""

import asyncio

import pytest

from laptop_server.voice.audio import AudioClip
from laptop_server.voice.service import VoiceService, find_piper_voices
from laptop_server.voice.tts.auto import AutoTTS, resample
from laptop_server.voice.tts.base import SynthesisResult, TTSError, TTSProvider, VoiceSettings
from tests.conftest import make_settings


class FakeEngine(TTSProvider):
    def __init__(self, name, rate=22050, fail=None, delay=0.0):
        self.name, self.rate, self.fail, self.delay = name, rate, fail, delay
        self.calls = []

    async def synthesize(self, text, voice):
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        return SynthesisResult(AudioClip.silence(0.1, self.rate), 0.01, 0.02)


def run(coro):
    return asyncio.run(coro)


V = VoiceSettings()


def test_azure_first_then_piper_fallback_with_circuit_breaker():
    now = [0.0]
    azure = FakeEngine("azure", fail=TTSError("HTTP 401"))
    en, ta = FakeEngine("en"), FakeEngine("ta")
    tts = AutoTTS(azure=azure, piper_en=en, piper_ta=ta, cache_size=0, clock=lambda: now[0])
    assert tts.name == "azure+piper"
    run(tts.synthesize("Hello there.", V))
    assert azure.calls == ["Hello there."] and en.calls == ["Hello there."] and tts.last_engine == "piper"
    run(tts.synthesize("Second sentence.", V))
    assert len(azure.calls) == 1  # skipped while the circuit is open
    now[0] = 61.0
    azure.fail = None
    run(tts.synthesize("Third.", V))
    assert len(azure.calls) == 2 and tts.last_engine == "azure"


def test_slow_azure_falls_back():
    azure = FakeEngine("azure", delay=1.0)
    en = FakeEngine("en")
    tts = AutoTTS(azure=azure, piper_en=en, azure_timeout_s=0.1, cache_size=0)
    run(tts.synthesize("Quick!", V))
    assert tts.last_engine == "piper"


def test_mixed_sentence_uses_both_piper_voices_and_joins_them():
    en, ta = FakeEngine("en", rate=22050), FakeEngine("ta", rate=16000)
    tts = AutoTTS(piper_en=en, piper_ta=ta, cache_size=0)
    r = run(tts.synthesize("சரி, உங்க Python error-ஐ பார்க்கலாம்", V))
    assert ta.calls and en.calls == ["Python"]
    assert r.audio.sample_rate == 16000  # first run's rate wins; others are resampled
    assert r.audio.duration_s > 0.25


def test_tamil_without_tamil_voice_errors_so_browser_takes_over():
    tts = AutoTTS(piper_en=FakeEngine("en"), cache_size=0)
    with pytest.raises(TTSError):
        run(tts.synthesize("வணக்கம்", V))


def test_short_phrases_are_cached():
    en = FakeEngine("en")
    tts = AutoTTS(piper_en=en, cache_size=8)
    run(tts.synthesize("Opening Chrome.", V))
    r = run(tts.synthesize("Opening Chrome.", V))
    assert en.calls == ["Opening Chrome."] and tts.last_engine == "cache" and r.total_s == 0.0


def test_resample_length():
    clip = AudioClip.silence(1.0, 22050)
    assert resample(clip, 16000).duration_s == pytest.approx(1.0, abs=0.01)


def test_voice_discovery(tmp_path):
    d = tmp_path / "voices"
    d.mkdir()
    (d / "en_US-lessac-medium.onnx").write_bytes(b"x")
    (d / "ta_IN-rasa_female-medium.onnx").write_bytes(b"x")
    s = make_settings(tts_piper_voices_dir=d)
    found = find_piper_voices(s)
    assert found["en"].name.startswith("en_US") and found["ta"].name.startswith("ta_IN")


def test_service_reports_combined_engine_name(tmp_path, monkeypatch):
    from pydantic import SecretStr

    d = tmp_path / "voices"
    d.mkdir()
    (d / "ta_IN-rasa_female-medium.onnx").write_bytes(b"x")
    monkeypatch.setattr("laptop_server.voice.service._installed", lambda m: True)
    s = make_settings(tts_provider="auto", tts_piper_voices_dir=d, azure_speech_key=SecretStr("k"))
    assert VoiceService(s).tts_name == "azure+piper"
    s2 = make_settings(tts_provider="auto", tts_piper_voices_dir=tmp_path / "none")
    assert VoiceService(s2).tts_name is None


# ------------------------------------------------------------------ sweet-girl voice style
def test_azure_ssml_uses_per_language_pitch():
    from laptop_server.voice.tts.azure import build_ssml

    v = VoiceSettings(rate=1.06, pitch_pct=18, strategy="split", extra={"ta_pitch_pct": 18, "en_pitch_pct": 4})
    ssml = build_ssml("சரி, Python பார்க்கலாம்", v, ta_voice="ta-IN-PallaviNeural", en_voice="en-US-AnaNeural")
    assert "ta-IN-PallaviNeural" in ssml and "en-US-AnaNeural" in ssml
    assert ssml.count("pitch='+18%'") + ssml.count('pitch="+18%"') >= 1
    assert "+4%" in ssml and "+6%" in ssml  # English pitch, rate


def test_girl_style_settings_and_capabilities(tmp_path):
    s = make_settings(tts_voice_style="girl")
    vs = VoiceService(s)
    v = vs.voice_settings()
    assert v.extra == {"ta_pitch_pct": 18.0, "en_pitch_pct": 4.0} and v.rate == 1.06
    assert vs.capabilities()["voice_style"] == "girl"
    assert VoiceService(make_settings()).voice_settings().extra == {}


def test_offline_voice_is_pitch_shifted_for_girl_style():
    np = pytest.importorskip("numpy")
    from laptop_server.voice.pitch import pitch_shift

    sr = 16000
    t = np.arange(sr) / sr
    tone = AudioClip(pcm16=(0.4 * np.sin(2 * np.pi * 200 * t) * 32767).astype("<i2").tobytes(), sample_rate=sr)
    out = pitch_shift(tone, 3.0)
    a = np.frombuffer(out.pcm16, dtype="<i2").astype(float)
    peak = np.fft.rfftfreq(len(a), 1 / sr)[np.argmax(np.abs(np.fft.rfft(a)))]
    assert len(a) == sr and abs(peak - 200 * 2 ** (3 / 12)) < 3

    class ToneEngine(FakeEngine):
        async def synthesize(self, text, voice):
            return SynthesisResult(tone, 0.0, 0.0)

    tts = AutoTTS(piper_en=ToneEngine("en"), piper_semitones=3.0, cache_size=0)
    r = run(tts.synthesize("Hello", V))
    b = np.frombuffer(r.audio.pcm16, dtype="<i2").astype(float)
    assert abs(np.fft.rfftfreq(len(b), 1 / sr)[np.argmax(np.abs(np.fft.rfft(b)))] - 238) < 3


def test_native_girl_pitch_asks_piper_to_speak_slower_then_speeds_up():
    """Optional method (AIRA_TTS_PIPER_PITCH_METHOD=native): Piper renders slower speech; playing it
    faster raises the pitch. Exact length only if the engine stretches fully (real Piper stretches
    less, which is why the default stays wsola)."""
    np = pytest.importorskip("numpy")
    sr, factor = 16000, 2 ** (3.5 / 12)

    class RateAwareTone(FakeEngine):
        async def synthesize(self, text, voice):
            self.calls.append(voice.rate)
            n = int(sr * 1.0 / voice.rate)                          # slower rate -> longer audio, same pitch
            t = np.arange(n) / sr
            clip = AudioClip(pcm16=(0.4 * np.sin(2 * np.pi * 200 * t) * 32767).astype("<i2").tobytes(), sample_rate=sr)
            return SynthesisResult(clip, 0.0, 0.0)

    eng = RateAwareTone("en")
    tts = AutoTTS(piper_en=eng, piper_semitones=3.5, cache_size=0, pitch_method="native")
    out = run(tts.synthesize("Hello", VoiceSettings(rate=1.0)))
    assert abs(eng.calls[0] - 1 / factor) < 1e-9                    # asked for slower speech
    a = np.frombuffer(out.audio.pcm16, dtype="<i2").astype(float)
    assert abs(len(a) / sr - 1.0) < 0.01                            # normal length again
    peak = np.fft.rfftfreq(len(a), 1 / sr)[np.argmax(np.abs(np.fft.rfft(a)))]
    assert abs(peak - 200 * factor) < 3                             # about 245 Hz: higher, younger


def test_text_is_made_speakable():
    from laptop_server.voice.text import speakable

    assert speakable("**Done!** It's 24°C 😊") == "Done! It's 24 degrees Celsius"
    assert speakable("See [the docs](https://x.y/z) or https://github.com") == "See the docs or a link"
    assert speakable("சரி! 👍 உங்க Python error-ஐ பார்க்கலாம்!") == "சரி! உங்க Python error-ஐ பார்க்கலாம்!"
    assert speakable("AIRA: CPU 12% & RAM 48%") == "Aira: CPU 12% and RAM 48%"
    assert speakable("- one\n- two\nDone.") == "one. two. Done."
    assert speakable("e.g. 1920x1080") == "for example 1920 by 1080"

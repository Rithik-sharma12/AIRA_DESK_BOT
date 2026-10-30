import asyncio
import xml.etree.ElementTree as ET

import httpx
import pytest

from laptop_server.voice.audio import AudioClip
from laptop_server.voice.tts import AzureTTS, TTSError, VoiceSettings, build_ssml

NS = {"s": "http://www.w3.org/2001/10/synthesis", "mstts": "https://www.w3.org/2001/mstts"}
TA, EN = "ta-IN-PallaviNeural", "en-IN-NeerjaNeural"


def voices(ssml: str) -> list[tuple[str, str]]:
    root = ET.fromstring(ssml)
    return [(v.get("name"), "".join(v.itertext())) for v in root.findall("s:voice", NS)]


def test_ta_only_uses_one_voice_with_prosody():
    ssml = build_ssml("சரி, Python error-ஐ பார்க்கலாம்!", VoiceSettings(), ta_voice=TA, en_voice=EN)
    assert voices(ssml) == [(TA, "சரி, Python error-ஐ பார்க்கலாம்!")]
    assert 'rate="+15%"' in ssml and 'pitch="+8%"' in ssml
    assert 'xml:lang="ta-IN"' in ssml


def test_split_switches_voices_and_styles_per_voice():
    ssml = build_ssml("சரி Python error-ஐ பார்க்கலாம்", VoiceSettings(strategy="split"),
                      ta_voice=TA, en_voice=EN, en_style="cheerful")
    assert voices(ssml) == [(TA, "சரி"), (EN, "Python"), (TA, "error-ஐ பார்க்கலாம்")]
    root = ET.fromstring(ssml)
    styled = root.findall(".//mstts:express-as", NS)
    assert len(styled) == 1 and styled[0].get("style") == "cheerful"


def test_auto_routes_english_only_to_english_voice():
    s = VoiceSettings(strategy="auto", rate=1.0, pitch_pct=-5)
    ssml = build_ssml("Good night!", s, ta_voice=TA, en_voice=EN)
    assert voices(ssml) == [(EN, "Good night!")]
    assert 'rate="+0%"' in ssml and 'pitch="-5%"' in ssml and 'xml:lang="en-IN"' in ssml
    assert voices(build_ssml("Good night வணக்கம்", s, ta_voice=TA, en_voice=EN))[0][0] == TA


def test_text_is_xml_escaped():
    ssml = build_ssml('a < b & "c"', VoiceSettings(), ta_voice=TA, en_voice=EN)
    ET.fromstring(ssml)  # parses
    assert "a &lt; b &amp;" in ssml


def test_requires_key():
    with pytest.raises(TTSError):
        AzureTTS(key="", region="centralindia")


def test_synthesize_request_and_result():
    wav = AudioClip.silence(0.5, 24000).to_wav_bytes()
    seen = {}

    def handler(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["headers"] = req.headers
        seen["body"] = req.content.decode()
        return httpx.Response(200, content=wav)

    tts = AzureTTS(key="k" * 32, region="centralindia", transport=httpx.MockTransport(handler))

    async def go():
        try:
            return await tts.synthesize("வணக்கம்!", VoiceSettings())
        finally:
            await tts.aclose()

    res = asyncio.run(go())
    assert seen["url"] == "https://centralindia.tts.speech.microsoft.com/cognitiveservices/v1"
    assert seen["headers"]["Ocp-Apim-Subscription-Key"] == "k" * 32
    assert seen["headers"]["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"
    assert "வணக்கம்" in seen["body"]
    assert res.audio.sample_rate == 24000 and res.audio.duration_s == pytest.approx(0.5)
    assert 0 < res.first_audio_s <= res.total_s


def test_synthesize_auth_error_has_hint():
    tts = AzureTTS(key="bad", region="centralindia",
                   transport=httpx.MockTransport(lambda r: httpx.Response(401, text="denied")))
    with pytest.raises(TTSError, match="check key/region"):
        asyncio.run(tts.synthesize("hi", VoiceSettings()))

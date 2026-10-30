"""Azure AI Speech neural TTS over its REST API (no SDK needed).

Endpoint: https://<region>.tts.speech.microsoft.com/cognitiveservices/v1
Voices used by default: ta-IN-PallaviNeural (Tamil, female) and en-IN-NeerjaNeural
(Indian English, female; supports the "cheerful" style). Several <voice> elements in
one SSML document let one request switch voices mid-sentence (the "split" strategy).
"""

from __future__ import annotations

import time
from xml.sax.saxutils import escape, quoteattr

import httpx

from ..audio import AudioClip
from ..text import dominant_language, split_by_script
from .base import SynthesisResult, TTSError, TTSProvider, VoiceSettings

OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"
LOCALE = {"ta": "ta-IN", "en": "en-IN"}


def _pct(value: float) -> str:
    return f"{'+' if value >= 0 else ''}{value:.0f}%"


def _voice_block(voice_name: str, text: str, s: VoiceSettings, style: str | None) -> str:
    inner = f'<prosody rate={quoteattr(_pct((s.rate - 1.0) * 100))} pitch={quoteattr(_pct(s.pitch_pct))}>{escape(text)}</prosody>'
    if style:
        inner = f"<mstts:express-as style={quoteattr(style)}>{inner}</mstts:express-as>"
    return f"<voice name={quoteattr(voice_name)}>{inner}</voice>"


def build_ssml(text: str, s: VoiceSettings, *, ta_voice: str, en_voice: str,
               ta_style: str | None = None, en_style: str | None = None) -> str:
    """SSML for one sentence according to the voicing strategy.

    Styles are per voice because a style one voice supports may not exist for another.
    `s.style`, if set, applies to the English voice when en_style is not given.
    """
    en_style = en_style or s.style
    if s.strategy == "split":
        runs = split_by_script(text)
        blocks = "".join(
            _voice_block(ta_voice if r.lang == "ta" else en_voice, r.text, s,
                         ta_style if r.lang == "ta" else en_style)
            for r in runs
        )
    elif s.strategy == "auto" and dominant_language(text) == "en":
        blocks = _voice_block(en_voice, text, s, en_style)
    else:  # ta_only, or auto with Tamil present
        blocks = _voice_block(ta_voice, text, s, ta_style)
    lang = LOCALE[dominant_language(text)]
    return (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">{blocks}</speak>'
    )


class AzureTTS(TTSProvider):
    name = "azure"
    offline = False

    def __init__(self, *, key: str, region: str, ta_voice: str = "ta-IN-PallaviNeural",
                 en_voice: str = "en-IN-NeerjaNeural", ta_style: str | None = None,
                 en_style: str | None = None, timeout_s: float = 20.0,
                 transport: httpx.AsyncBaseTransport | None = None):
        if not key or not region:
            raise TTSError("Azure TTS needs AIRA_AZURE_SPEECH_KEY and AIRA_AZURE_SPEECH_REGION")
        self.ta_voice, self.en_voice = ta_voice, en_voice
        self.ta_style, self.en_style = ta_style, en_style
        self._client = httpx.AsyncClient(
            base_url=f"https://{region}.tts.speech.microsoft.com",
            timeout=timeout_s,
            transport=transport,
            headers={
                "Ocp-Apim-Subscription-Key": key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": OUTPUT_FORMAT,
                "User-Agent": "aira-desk-bot",
            },
        )

    def ssml(self, text: str, voice: VoiceSettings) -> str:
        return build_ssml(text, voice, ta_voice=self.ta_voice, en_voice=self.en_voice,
                          ta_style=self.ta_style, en_style=self.en_style)

    async def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        body = self.ssml(text, voice).encode("utf-8")
        t0 = time.perf_counter()
        first: float | None = None
        chunks: list[bytes] = []
        try:
            async with self._client.stream("POST", "/cognitiveservices/v1", content=body) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode("utf-8", "replace")[:300]
                    hint = " (check key/region)" if r.status_code in (401, 403) else ""
                    raise TTSError(f"Azure TTS HTTP {r.status_code}{hint}: {detail}")
                async for chunk in r.aiter_bytes():
                    if chunk and first is None:
                        first = time.perf_counter() - t0
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise TTSError(f"Azure TTS request failed: {exc.__class__.__name__}") from exc
        total = time.perf_counter() - t0
        return SynthesisResult(AudioClip.from_wav_bytes(b"".join(chunks)), first or total, total)

    async def aclose(self) -> None:
        await self._client.aclose()

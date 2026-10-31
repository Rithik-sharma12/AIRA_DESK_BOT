"""Server-side speech for the web interface: speech-to-text and text-to-speech.

Both are optional. If faster-whisper is not installed, or no TTS engine is configured,
the capabilities endpoint says so and the web page uses the browser's own speech
recognition / speech synthesis instead. Heavy models load lazily on first use.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
from pathlib import Path

from ..config import Settings
from .audio import AudioClip
from .stt import FasterWhisperSTT, STTError, STTProvider, Transcript
from .tts import TTSError, TTSProvider, VoiceSettings, create_tts

log = logging.getLogger("aira.voice")


class VoiceUnavailable(Exception):
    """No server-side engine for this direction; the browser should do it."""


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


class VoiceService:
    def __init__(self, settings: Settings, *, stt: STTProvider | None = None, tts: TTSProvider | None = None):
        self.s = settings
        self._stt = stt
        self._tts = tts
        self._stt_lock = asyncio.Lock()
        self._stt_error: str | None = None
        self._tts_error: str | None = None

    # ------------------------------------------------------------ capabilities
    @property
    def stt_name(self) -> str | None:
        if self._stt is not None:
            return getattr(self._stt, "name", "custom")
        if self.s.stt_provider == "none" or self._stt_error:
            return None
        return "faster-whisper" if _installed("faster_whisper") else None

    @property
    def tts_name(self) -> str | None:
        if self._tts is not None:
            return getattr(self._tts, "name", "custom")
        if self._tts_error:
            return None
        p = self.s.tts_provider
        has_key = bool(self.s.azure_speech_key.get_secret_value())
        has_piper = bool(self.s.tts_piper_model) and Path(self.s.tts_piper_model).exists() and _installed("piper")
        if p in ("auto", "azure") and has_key:
            return "azure"
        if p in ("auto", "piper") and has_piper:
            return "piper"
        return None

    def capabilities(self) -> dict:
        return {
            "stt": self.stt_name,
            "stt_model": self.s.stt_model if self.stt_name == "faster-whisper" else None,
            "tts": self.tts_name,
            "stt_error": self._stt_error,
            "tts_error": self._tts_error,
        }

    # ---------------------------------------------------------------- engines
    def _load_stt(self) -> STTProvider:
        if self._stt is None:
            if self.stt_name is None:
                raise VoiceUnavailable("No server speech recognition (install faster-whisper, see HOW-TO-RUN.md)")
            log.info("Loading faster-whisper %s on %s ...", self.s.stt_model, self.s.stt_device)
            try:
                self._stt = FasterWhisperSTT(self.s.stt_model, device=self.s.stt_device,
                                             compute_type=self.s.stt_compute_type)
            except Exception as exc:  # CUDA libraries missing, model download failed, ...
                self._stt_error = f"{exc.__class__.__name__}: {exc}"[:300]
                log.error("faster-whisper failed to load: %s", self._stt_error)
                raise VoiceUnavailable(self._stt_error) from exc
            log.info("faster-whisper ready (%.1f s)", getattr(self._stt, "load_s", 0.0))
        return self._stt

    def _load_tts(self) -> TTSProvider:
        if self._tts is None:
            name = self.tts_name
            if name is None:
                raise VoiceUnavailable("No server text-to-speech configured (Azure key or Piper model)")
            try:
                if name == "azure":
                    self._tts = create_tts("azure", key=self.s.azure_speech_key.get_secret_value(),
                                           region=self.s.azure_speech_region, ta_voice=self.s.tts_ta_voice,
                                           en_voice=self.s.tts_en_voice, en_style="cheerful")
                else:
                    self._tts = create_tts("piper", model_path=self.s.tts_piper_model)
            except (TTSError, ImportError, OSError) as exc:
                self._tts_error = str(exc)[:300]
                raise VoiceUnavailable(self._tts_error) from exc
        return self._tts

    # ------------------------------------------------------------- operations
    async def transcribe(self, path: Path, language: str | None) -> Transcript:
        async with self._stt_lock:  # one GPU job at a time
            stt = await asyncio.to_thread(self._load_stt)
            try:
                return await asyncio.to_thread(stt.transcribe, path, language)
            except STTError as exc:
                raise VoiceUnavailable(str(exc)) from exc

    async def speak(self, text: str) -> AudioClip:
        tts = self._load_tts()
        voice = VoiceSettings(rate=self.s.tts_rate, pitch_pct=self.s.tts_pitch_pct, strategy=self.s.tts_strategy)
        try:
            return (await tts.synthesize(text, voice)).audio
        except TTSError as exc:
            raise VoiceUnavailable(str(exc)) from exc

    async def aclose(self) -> None:
        if self._tts is not None:
            await self._tts.aclose()

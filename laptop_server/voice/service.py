"""Server-side speech for the web interface: speech-to-text and text-to-speech.

Both are optional. If faster-whisper is not installed, or no TTS engine is configured,
the capabilities endpoint says so and the web page uses the browser's own speech
recognition / speech synthesis instead. Heavy models load lazily on first use.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import threading
from pathlib import Path

from ..config import Settings
from .audio import AudioClip
from .stt import FasterWhisperSTT, STTError, STTProvider, Transcript
from .text import speakable
from .tts import TTSError, TTSProvider, VoiceSettings, create_tts
from .tts.auto import AutoTTS

log = logging.getLogger("aira.voice")


class VoiceUnavailable(Exception):
    """No server-side engine for this direction; the browser should do it."""


def _installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def find_piper_voices(settings: Settings) -> dict[str, Path]:
    """English and Tamil Piper voices: explicit settings first, then voices/ (download_voices.py)."""
    found: dict[str, Path] = {}
    for lang, explicit in (("en", settings.tts_piper_en_model), ("ta", settings.tts_piper_ta_model)):
        if explicit and Path(explicit).exists():
            found[lang] = Path(explicit)
    legacy = settings.tts_piper_model
    if legacy and Path(legacy).exists():
        found.setdefault("ta" if Path(legacy).name.lower().startswith("ta") else "en", Path(legacy))
    d = settings.tts_piper_voices_dir
    if d and Path(d).is_dir():
        for lang in ("en", "ta"):
            if lang not in found:
                hits = sorted(Path(d).glob(f"{lang}_*.onnx"))
                if hits:
                    found[lang] = hits[0]
    return found


class VoiceService:
    def __init__(self, settings: Settings, *, stt: STTProvider | None = None, tts: TTSProvider | None = None):
        self.s = settings
        self._stt = stt
        self._tts = tts
        self._stt_lock = asyncio.Lock()
        self._stt_error: str | None = None
        self._tts_error: str | None = None
        self._tts_load_lock = threading.Lock()   # start-up warm-up and a first request may race

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
        engines = self._tts_plan()
        return "+".join(engines) if engines else None

    def _tts_plan(self) -> list[str]:
        p = self.s.tts_provider
        if p == "none":
            return []
        plan = []
        if p in ("auto", "azure") and self.s.azure_speech_key.get_secret_value():
            plan.append("azure")
        if p in ("auto", "piper") and find_piper_voices(self.s) and _installed("piper"):
            plan.append("piper")
        return plan

    def capabilities(self) -> dict:
        return {
            "stt": self.stt_name,
            "stt_model": self.s.stt_model if self.stt_name == "faster-whisper" else None,
            "tts": self.tts_name,
            "voice_style": self.s.tts_voice_style,
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
        with self._tts_load_lock:
            return self._load_tts_locked()

    def _load_tts_locked(self) -> TTSProvider:
        if self._tts is None:
            plan = self._tts_plan()
            if not plan:
                raise VoiceUnavailable("No server text-to-speech configured (Azure key or Piper voices)")
            try:
                azure = None
                girl = self.s.tts_voice_style == "girl"
                if "azure" in plan:
                    azure = create_tts("azure", key=self.s.azure_speech_key.get_secret_value(),
                                       region=self.s.azure_speech_region, ta_voice=self.s.tts_ta_voice,
                                       en_voice=self.s.tts_girl_en_voice if girl else self.s.tts_en_voice,
                                       en_style=None if girl else "cheerful")
                piper: dict[str, TTSProvider] = {}
                if "piper" in plan:
                    for lang, path in find_piper_voices(self.s).items():
                        try:
                            piper[lang] = create_tts("piper", model_path=path, noise_scale=self.s.tts_piper_noise_scale,
                                                     noise_w=self.s.tts_piper_noise_w)
                            log.info("Piper %s voice loaded: %s", lang, path.name)
                        except (TTSError, ImportError, OSError) as exc:
                            log.error("Piper voice %s failed to load: %s", path, exc)
                self._tts = AutoTTS(azure=azure, piper_en=piper.get("en"), piper_ta=piper.get("ta"),
                                    azure_timeout_s=self.s.tts_azure_timeout_s, cache_size=self.s.tts_cache_size,
                                    piper_semitones=self.s.tts_girl_piper_semitones if girl else 0.0,
                                    pitch_method=self.s.tts_piper_pitch_method)
            except (TTSError, ImportError, OSError) as exc:
                self._tts_error = str(exc)[:300]
                raise VoiceUnavailable(self._tts_error) from exc
        return self._tts

    async def warmup(self) -> None:
        """Load voices in the background at start-up so the first reply isn't delayed."""
        if self._tts is None and self._tts_plan():
            try:
                await asyncio.to_thread(self._load_tts)
            except VoiceUnavailable as exc:
                log.error("Voice not available: %s", exc)
                return
            # One short sentence per voice: the first real reply then plays without the cold start.
            for text in ("Hi!", "சரி!"):
                try:
                    await self._tts.synthesize(text, self.voice_settings())
                except Exception as exc:  # warm-up must never stop the server
                    log.debug("voice warm-up (%s) failed: %s", text, exc)

    # ------------------------------------------------------------- operations
    async def transcribe(self, path: Path, language: str | None) -> Transcript:
        async with self._stt_lock:  # one GPU job at a time
            stt = await asyncio.to_thread(self._load_stt)
            try:
                return await asyncio.to_thread(stt.transcribe, path, language)
            except STTError as exc:
                raise VoiceUnavailable(str(exc)) from exc

    def voice_settings(self) -> VoiceSettings:
        s = self.s
        if s.tts_voice_style == "girl":
            return VoiceSettings(rate=s.tts_girl_rate, pitch_pct=s.tts_girl_ta_pitch_pct, strategy=s.tts_strategy,
                                 extra={"ta_pitch_pct": s.tts_girl_ta_pitch_pct, "en_pitch_pct": s.tts_girl_en_pitch_pct})
        return VoiceSettings(rate=s.tts_rate, pitch_pct=s.tts_pitch_pct, strategy=s.tts_strategy)

    async def speak(self, text: str) -> AudioClip:
        tts = self._load_tts()
        voice = self.voice_settings()
        text = speakable(text) or text
        try:
            return (await tts.synthesize(text, voice)).audio
        except TTSError as exc:
            raise VoiceUnavailable(str(exc)) from exc

    async def aclose(self) -> None:
        if self._tts is not None:
            await self._tts.aclose()

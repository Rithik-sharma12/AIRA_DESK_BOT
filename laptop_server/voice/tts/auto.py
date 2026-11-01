"""Speech output that is always available, as fast and clear as the moment allows.

``AutoTTS`` tries Azure's neural voices first (clearest Tamil and English). If Azure is
not configured, the network is down, or it answers slower than ``azure_timeout_s``, the
sentence is spoken by the offline Piper voices instead, and Azure is skipped for a
minute so later sentences don't wait for it again.

Piper speaks one language per voice, so a mixed sentence is split by script: Tamil
words go to the Tamil voice, English words to the English voice, and the pieces are
joined with a tiny gap. Short phrases ("Opening Chrome.") are cached in memory and
replay instantly.
"""

from __future__ import annotations

import array
import asyncio
import logging
import time
from collections import OrderedDict

from ..audio import AudioClip, concat
from dataclasses import replace

from ..pitch import pitch_shift, speed_up
from ..text import split_by_script
from .base import SynthesisResult, TTSError, TTSProvider, VoiceSettings

log = logging.getLogger("aira.voice.tts")
CIRCUIT_OPEN_S = 60.0
CACHE_MAX_CHARS = 120


def resample(clip: AudioClip, rate: int) -> AudioClip:
    """Linear-interpolation resample of mono 16-bit audio (good enough for joining voices)."""
    if clip.sample_rate == rate:
        return clip
    src = array.array("h")
    src.frombytes(clip.pcm16)
    n_out = max(1, int(len(src) * rate / clip.sample_rate))
    step = (len(src) - 1) / max(1, n_out - 1)
    out = array.array("h", bytes(2 * n_out))
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        nxt = src[j + 1] if j + 1 < len(src) else src[j]
        out[i] = int(src[j] + (nxt - src[j]) * frac)
    return AudioClip(pcm16=out.tobytes(), sample_rate=rate, channels=1)


class AutoTTS(TTSProvider):
    offline = False

    def __init__(self, *, azure: TTSProvider | None = None, piper_en: TTSProvider | None = None,
                 piper_ta: TTSProvider | None = None, azure_timeout_s: float = 4.0, cache_size: int = 64,
                 piper_semitones: float = 0.0, pitch_method: str = "wsola", clock=time.monotonic):
        if not (azure or piper_en or piper_ta):
            raise TTSError("no TTS engine available")
        self.azure = azure
        self.piper = {"en": piper_en, "ta": piper_ta}
        self.azure_timeout_s = azure_timeout_s
        self.piper_semitones = piper_semitones
        self.pitch_method = pitch_method      # wsola: time-stretch (voice A); native: Piper slower, then sped up
        self.cache_size = cache_size
        self._cache: OrderedDict[tuple, SynthesisResult] = OrderedDict()
        self._azure_down_until = 0.0
        self._clock = clock
        self.last_engine: str | None = None

    @property
    def name(self) -> str:  # type: ignore[override]
        parts = (["azure"] if self.azure else []) + (["piper"] if any(self.piper.values()) else [])
        return "+".join(parts)

    @property
    def has_piper(self) -> bool:
        return any(self.piper.values())

    # ------------------------------------------------------------------ public
    async def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        key = (text, voice.rate, voice.pitch_pct, voice.strategy)
        if len(text) <= CACHE_MAX_CHARS and key in self._cache:
            self._cache.move_to_end(key)
            self.last_engine = "cache"
            cached = self._cache[key]
            return SynthesisResult(cached.audio, 0.0, 0.0)
        result = await self._synthesize(text, voice)
        if self.cache_size and len(text) <= CACHE_MAX_CHARS:
            self._cache[key] = result
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return result

    async def _synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        azure_error: Exception | None = None
        if self.azure and self._clock() >= self._azure_down_until:
            try:
                result = await asyncio.wait_for(self.azure.synthesize(text, voice), timeout=self.azure_timeout_s)
                self.last_engine = "azure"
                return result
            except Exception as exc:  # network, auth, quota, timeout: all fall back to Piper
                azure_error = exc
                if self.has_piper:
                    self._azure_down_until = self._clock() + CIRCUIT_OPEN_S
                    log.warning("Azure TTS unavailable (%s); using offline Piper for the next %.0f s",
                                exc.__class__.__name__, CIRCUIT_OPEN_S)
        if self.has_piper:
            try:
                result = await self._piper(text, voice)
                self.last_engine = "piper"
                return result
            except TTSError:
                if azure_error is None:
                    raise
        raise TTSError(f"speech failed: {azure_error}") if azure_error else TTSError("no voice for this text")

    async def _piper(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        t0 = time.perf_counter()
        runs = split_by_script(text, default="en")
        en, ta = self.piper["en"], self.piper["ta"]
        clips: list[AudioClip] = []
        first: float | None = None
        factor = 2 ** (self.piper_semitones / 12) if self.piper_semitones else 1.0
        native = factor != 1.0 and self.pitch_method == "native"
        run_voice = replace(voice, rate=voice.rate / factor) if native else voice
        for run in runs:
            engine = ta if run.lang == "ta" else (en or ta)
            if engine is None:
                raise TTSError("no Tamil Piper voice installed (python scripts/download_voices.py)")
            r = await engine.synthesize(run.text, run_voice)
            first = first if first is not None else time.perf_counter() - t0
            clips.append(r.audio)
        if not clips:
            raise TTSError("nothing to say")
        rate = clips[0].sample_rate
        clip = clips[0] if len(clips) == 1 else concat([resample(c, rate) for c in clips], gap_s=0.06)
        if native:
            clip = await asyncio.to_thread(speed_up, clip, factor)
        elif self.piper_semitones:
            clip = await asyncio.to_thread(pitch_shift, clip, self.piper_semitones)
        return SynthesisResult(clip, first or 0.0, time.perf_counter() - t0)

    async def aclose(self) -> None:
        for engine in (self.azure, *self.piper.values()):
            if engine is not None:
                await engine.aclose()

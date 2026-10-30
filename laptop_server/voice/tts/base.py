"""Provider-neutral text-to-speech interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal

from ..audio import AudioClip

# How a mixed Tamil/English sentence is voiced:
#   ta_only - the whole sentence in the Tamil voice (one timbre; English gets a Tamil Nadu accent)
#   split   - Tamil runs in the Tamil voice, English runs in the English voice
#   auto    - English-only sentences in the English voice, anything with Tamil in the Tamil voice
Strategy = Literal["ta_only", "split", "auto"]


@dataclass
class VoiceSettings:
    rate: float = 1.15                 # 1.0 = normal; brief asks for about 1.1-1.2
    pitch_pct: float = 8.0             # relative pitch change, + is higher (more youthful)
    style: str | None = None           # engine-specific speaking style, e.g. Azure "cheerful"
    strategy: Strategy = "ta_only"
    extra: dict = field(default_factory=dict)


@dataclass
class SynthesisResult:
    audio: AudioClip
    first_audio_s: float               # time until the first audio byte was available
    total_s: float                     # time until synthesis finished

    @property
    def real_time_factor(self) -> float:
        d = self.audio.duration_s
        return self.total_s / d if d else 0.0


class TTSError(Exception):
    pass


class TTSProvider(ABC):
    name = "abstract"
    offline = False

    @abstractmethod
    async def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult: ...

    async def aclose(self) -> None:
        return None

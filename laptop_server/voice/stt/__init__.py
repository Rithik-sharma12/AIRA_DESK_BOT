"""Speech-to-text providers."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Transcript:
    text: str
    language: str | None
    language_probability: float | None
    audio_s: float
    latency_s: float

    @property
    def real_time_factor(self) -> float:
        return self.latency_s / self.audio_s if self.audio_s else 0.0


class STTError(Exception):
    pass


class STTProvider(ABC):
    name = "abstract"

    @abstractmethod
    def transcribe(self, wav_path: str | Path, language: str | None = None) -> Transcript: ...


class FasterWhisperSTT(STTProvider):
    """faster-whisper (CTranslate2). Model names: "large-v3", "large-v3-turbo", "medium", ...

    GPU use needs the CUDA 12 cuBLAS and cuDNN libraries that CTranslate2 expects (see the
    faster-whisper README's GPU section). device="cpu", compute_type="int8" works anywhere.
    """

    name = "faster-whisper"

    def __init__(self, model: str = "large-v3", device: str = "cuda", compute_type: str = "float16",
                 beam_size: int = 5, vad_filter: bool = True, initial_prompt: str | None = None):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError("faster-whisper not installed: pip install faster-whisper") from exc
        t0 = time.perf_counter()
        self.model = WhisperModel(model, device=device, compute_type=compute_type)
        self.load_s = time.perf_counter() - t0
        self.model_name = model
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        # A short prompt with the words AIRA hears often nudges spelling (Tamil script + English terms).
        self.initial_prompt = initial_prompt

    def transcribe(self, wav_path: str | Path, language: str | None = None) -> Transcript:
        t0 = time.perf_counter()
        segments, info = self.model.transcribe(
            str(wav_path), language=language, beam_size=self.beam_size,
            vad_filter=self.vad_filter, initial_prompt=self.initial_prompt,
        )
        text = "".join(s.text for s in segments).strip()  # segments is lazy; joining runs decoding
        return Transcript(
            text=text,
            language=info.language,
            language_probability=getattr(info, "language_probability", None),
            audio_s=getattr(info, "duration", 0.0),
            latency_s=time.perf_counter() - t0,
        )

"""Minimal PCM audio container shared by TTS and STT code (stdlib only)."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AudioClip:
    pcm16: bytes            # little-endian signed 16-bit samples
    sample_rate: int
    channels: int = 1

    @property
    def duration_s(self) -> float:
        frames = len(self.pcm16) // (2 * self.channels)
        return frames / self.sample_rate if self.sample_rate else 0.0

    def to_wav_bytes(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(2)
            w.setframerate(self.sample_rate)
            w.writeframes(self.pcm16)
        return buf.getvalue()

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(self.to_wav_bytes())

    @classmethod
    def from_wav_bytes(cls, data: bytes) -> "AudioClip":
        with wave.open(io.BytesIO(data), "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError(f"expected 16-bit PCM WAV, got {8 * w.getsampwidth()}-bit")
            return cls(pcm16=w.readframes(w.getnframes()), sample_rate=w.getframerate(), channels=w.getnchannels())

    @classmethod
    def load(cls, path: str | Path) -> "AudioClip":
        return cls.from_wav_bytes(Path(path).read_bytes())

    @classmethod
    def silence(cls, seconds: float, sample_rate: int, channels: int = 1) -> "AudioClip":
        return cls(pcm16=b"\x00\x00" * int(seconds * sample_rate) * channels, sample_rate=sample_rate, channels=channels)

    @classmethod
    def from_float(cls, samples, sample_rate: int) -> "AudioClip":
        """From a sequence/array of floats in [-1, 1] (numpy optional)."""
        try:
            import numpy as np

            arr = np.clip(np.asarray(samples, dtype="float32"), -1.0, 1.0)
            return cls(pcm16=(arr * 32767).astype("<i2").tobytes(), sample_rate=sample_rate)
        except ImportError:  # pragma: no cover - numpy is present wherever float audio is produced
            import array

            a = array.array("h", (int(max(-1.0, min(1.0, s)) * 32767) for s in samples))
            return cls(pcm16=a.tobytes(), sample_rate=sample_rate)


def concat(clips: list[AudioClip], gap_s: float = 0.0) -> AudioClip:
    if not clips:
        raise ValueError("nothing to concatenate")
    rate, ch = clips[0].sample_rate, clips[0].channels
    if any(c.sample_rate != rate or c.channels != ch for c in clips):
        raise ValueError("all clips must share sample rate and channel count")
    gap = AudioClip.silence(gap_s, rate, ch).pcm16 if gap_s > 0 else b""
    return AudioClip(pcm16=gap.join(c.pcm16 for c in clips), sample_rate=rate, channels=ch)

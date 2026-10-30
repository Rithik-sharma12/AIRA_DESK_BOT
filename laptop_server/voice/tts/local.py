"""Offline TTS engines that run on the laptop: Piper (CPU) and AI4Bharat Indic Parler-TTS (GPU).

Both are optional, heavy dependencies imported only when used (see requirements-voice.txt).
They have not been run in the build environment (no GPU/models there); verify on first use.
Synthesis runs in a worker thread so the server's event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import io
import time
import wave
from pathlib import Path

from ..audio import AudioClip
from ..text import split_sentences
from .base import SynthesisResult, TTSError, TTSProvider, VoiceSettings


class PiperTTS(TTSProvider):
    """Piper ONNX voice, e.g. the community ta_IN Rasa female/male models.

    Piper speaks one language per model and has no pitch control; `rate` maps to
    length_scale = 1 / rate. English words inside Tamil text go through eSpeak's Tamil
    phonemiser, so expect them to sound off — that is part of what the bake-off measures.
    """

    name = "piper"
    offline = True

    def __init__(self, *, model_path: str | Path, use_cuda: bool = False):
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise TTSError("Piper not installed: pip install piper-tts") from exc
        path = Path(model_path)
        if not path.exists():
            raise TTSError(f"Piper model not found: {path} (download the .onnx and .onnx.json pair)")
        self._voice = PiperVoice.load(str(path), use_cuda=use_cuda)

    def _run(self, text: str, voice: VoiceSettings) -> AudioClip:
        buf = io.BytesIO()
        length_scale = 1.0 / max(voice.rate, 0.1)
        with wave.open(buf, "wb") as wav:
            if hasattr(self._voice, "synthesize_wav"):  # piper-tts >= 1.3
                try:
                    from piper import SynthesisConfig

                    self._voice.synthesize_wav(text, wav, syn_config=SynthesisConfig(length_scale=length_scale))
                except ImportError:
                    self._voice.synthesize_wav(text, wav)
            else:  # older piper-tts API
                self._voice.synthesize(text, wav, length_scale=length_scale)
        return AudioClip.from_wav_bytes(buf.getvalue())

    async def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        t0 = time.perf_counter()
        clip = await asyncio.to_thread(self._run, text, voice)
        total = time.perf_counter() - t0
        return SynthesisResult(clip, total, total)


DEFAULT_PARLER_DESCRIPTION = (
    "Kavya speaks in a cheerful, expressive and youthful voice at a slightly fast pace. "
    "The recording is very clear, with no background noise."
)


class IndicParlerTTS(TTSProvider):
    """AI4Bharat Indic Parler-TTS: the voice is described in plain English text.

    `voice.extra["description"]` overrides the description. Speed and pitch are steered
    through the description ("slightly fast pace", "high-pitched"), not numeric settings.
    """

    name = "indic-parler"
    offline = True

    def __init__(self, *, model_id: str = "ai4bharat/indic-parler-tts", device: str = "cuda"):
        try:
            import torch
            from parler_tts import ParlerTTSForConditionalGeneration
            from transformers import AutoTokenizer
        except ImportError as exc:
            raise TTSError(
                "Indic Parler-TTS not installed: pip install git+https://github.com/huggingface/parler-tts.git "
                "(and a CUDA build of torch)"
            ) from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise TTSError("CUDA not available for Indic Parler-TTS; use device='cpu' (slow)")
        self._torch = torch
        self.device = device
        self.model = ParlerTTSForConditionalGeneration.from_pretrained(model_id).to(device)
        self.tokenizer = AutoTokenizer.from_pretrained(model_id)
        self.desc_tokenizer = AutoTokenizer.from_pretrained(self.model.config.text_encoder._name_or_path)
        self.sample_rate = self.model.config.sampling_rate

    def _run(self, text: str, voice: VoiceSettings) -> AudioClip:
        description = voice.extra.get("description", DEFAULT_PARLER_DESCRIPTION)
        desc = self.desc_tokenizer(description, return_tensors="pt").to(self.device)
        clips = []
        # Parler degrades on long inputs; generate per sentence.
        for sentence in split_sentences(text) or [text]:
            prompt = self.tokenizer(sentence, return_tensors="pt").to(self.device)
            with self._torch.inference_mode():
                gen = self.model.generate(
                    input_ids=desc.input_ids,
                    attention_mask=desc.attention_mask,
                    prompt_input_ids=prompt.input_ids,
                    prompt_attention_mask=prompt.attention_mask,
                )
            clips.append(AudioClip.from_float(gen.cpu().float().numpy().squeeze(), self.sample_rate))
        from ..audio import concat

        return concat(clips, gap_s=0.12)

    async def synthesize(self, text: str, voice: VoiceSettings) -> SynthesisResult:
        t0 = time.perf_counter()
        clip = await asyncio.to_thread(self._run, text, voice)
        total = time.perf_counter() - t0
        return SynthesisResult(clip, total, total)

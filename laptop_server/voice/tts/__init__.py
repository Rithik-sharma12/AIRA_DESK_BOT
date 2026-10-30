from .base import Strategy, SynthesisResult, TTSError, TTSProvider, VoiceSettings  # noqa: F401
from .azure import AzureTTS, build_ssml  # noqa: F401


def create_tts(provider: str, **kwargs) -> TTSProvider:
    """Factory used by the bake-off and (from Phase 4) the voice service."""
    if provider == "azure":
        return AzureTTS(**kwargs)
    if provider == "piper":
        from .local import PiperTTS

        return PiperTTS(**kwargs)
    if provider in ("parler", "indic-parler"):
        from .local import IndicParlerTTS

        return IndicParlerTTS(**kwargs)
    raise TTSError(f"unknown TTS provider: {provider}")

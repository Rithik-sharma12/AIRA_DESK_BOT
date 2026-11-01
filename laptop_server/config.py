"""Central configuration for the AIRA laptop server.

Every setting can be overridden with an environment variable prefixed ``AIRA_``
(for example ``AIRA_LLM_MODEL=gemma4:12b-it-qat``) or in the ``.env`` file.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MIN_API_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Server -----------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)
    allow_lan: bool = Field(
        False,
        description="Must be true to bind to a non-loopback address (needed from Phase 6, when the Pi connects).",
    )
    api_key: SecretStr = SecretStr("")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path | None = Field(Path("logs"), description="None disables file logs")
    log_conversation_content: bool = Field(
        False, description="Write user/assistant text to logs. Off by default for privacy."
    )

    # ---- LLM ------------------------------------------------------------------
    llm_provider: Literal["ollama", "online", "auto"] = Field(
        "ollama", description="ollama: the local model only; online: an online AI with your API key; "
                              "auto: local first, online when Ollama is not reachable")
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "gemma4:e4b-it-qat"
    llm_num_ctx: int = Field(4096, ge=512, le=262144)
    llm_temperature: float = Field(0.7, ge=0.0, le=2.0)
    llm_top_p: float = Field(0.95, gt=0.0, le=1.0)
    llm_max_tokens: int = Field(400, ge=16, le=8192, description="Ollama num_predict")
    llm_keep_alive: str = Field("-1", description="How long Ollama keeps the model in VRAM after a request. "
                                                  "-1: as long as AIRA runs (no reload delay; freed when AIRA stops). "
                                                  "e.g. 30m to free the GPU sooner")
    llm_think: bool | None = Field(
        False,
        description=(
            "Ollama's 'think' flag. Gemma 4 and Qwen 3.5 reason silently before answering by default, "
            "which makes replies slow and can use up the whole reply budget. False (default) turns it off; "
            "True turns it on; 'default' leaves the model's own behaviour. Models that don't support the flag "
            "are retried without it automatically."
        ),
    )
    llm_request_timeout_s: float = Field(120.0, gt=0)

    # ---- Online AI (llm_provider=online or auto) -------------------------------
    online_service: str = Field("", description="openai, gemini, groq, openrouter, deepseek, mistral, together, "
                                                "anthropic; or leave empty and set online_base_url")
    online_base_url: str = Field("", description="Any OpenAI-compatible API, e.g. https://api.openai.com/v1")
    online_model: str = Field("", description="Model name from your service's model list")
    online_api_key: SecretStr = SecretStr("")
    online_timeout_s: float = Field(60.0, gt=0)
    llm_warmup_on_start: bool = True

    # ---- Conversation ---------------------------------------------------------
    user_name: str = Field("", max_length=40, description="How AIRA addresses you")
    tamil_output_script: Literal["tamil", "latin"] = Field(
        "tamil",
        description="Write Tamil words in Tamil script (best for TTS) or in Latin letters (Tanglish).",
    )
    persona_file: Path | None = Field(None, description="Optional text file replacing the built-in persona prompt")
    history_max_turns: int = Field(12, ge=1, le=200)
    history_token_budget: int = Field(3000, ge=256)
    session_ttl_minutes: int = Field(60, ge=1)
    max_sessions: int = Field(50, ge=1)
    max_user_message_chars: int = Field(4000, ge=1, le=32000)

    # ---- Task automation ----------------------------------------------------------
    agent_enabled: bool = Field(True, description="Let AIRA act on the laptop (apps, volume, reminders...)")
    agent_dry_run: bool | None = Field(
        None, description="true = only log actions. Default: real actions on Windows, dry-run elsewhere.")
    agent_apps_file: Path | None = Field(Path("config/apps.json"),
                                         description="Extra apps/websites AIRA may open (optional)")
    agent_fast_commands: bool = Field(True, description="Handle 'volume up', 'open chrome'... without the model")
    agent_max_tool_rounds: int = Field(3, ge=1, le=6)

    # ---- Voice (Phase 2 bake-off; voice pipeline in Phase 4) -------------------
    azure_speech_key: SecretStr = SecretStr("")
    azure_speech_region: str = Field("centralindia", description="Azure region of the Speech resource")
    tts_ta_voice: str = "ta-IN-PallaviNeural"
    tts_en_voice: str = "en-IN-NeerjaNeural"

    # ---- Web voice interface (prototype) ----------------------------------------
    web_ui_enabled: bool = True
    data_dir: Path = Field(Path("data"), description="Runtime data (paired web devices). Git-ignored.")
    ssl_certfile: Path | None = Field(None, description="HTTPS certificate (scripts/make_dev_cert.py)")
    ssl_keyfile: Path | None = None
    stt_provider: Literal["auto", "faster-whisper", "none"] = Field(
        "auto", description="auto = faster-whisper if installed, else the browser does speech recognition")
    stt_model: str = "large-v3-turbo"
    stt_device: str = Field("auto", description="auto, cuda or cpu")
    stt_compute_type: str = Field("default", description="e.g. float16, int8_float16, int8")
    tts_provider: Literal["auto", "azure", "piper", "none"] = Field(
        "auto", description="auto = Azure if a key is set, else Piper if a model is set, else the browser speaks")
    tts_piper_model: Path | None = Field(None, description="Single Piper voice (older setting)")
    tts_piper_voices_dir: Path = Field(Path("voices"), description="scripts/download_voices.py puts voices here")
    tts_piper_en_model: Path | None = Field(None, description="English Piper voice; default: first en_*.onnx")
    tts_piper_ta_model: Path | None = Field(None, description="Tamil Piper voice; default: first ta_*.onnx")
    tts_azure_timeout_s: float = Field(4.0, gt=0.5, le=30,
                                       description="After this, fall back to Piper for that sentence")
    tts_cache_size: int = Field(64, ge=0, le=1000, description="Short phrases kept in memory for instant replay")
    tts_voice_style: Literal["default", "girl"] = Field(
        "default", description="girl = sweet young-girl voice: Azure's child voice for English, higher pitch for "
        "Tamil, pitch-shifted offline voices, higher browser voice")
    tts_girl_en_voice: str = Field("en-US-AnaNeural", description="Azure child voice for English sentences")
    tts_girl_ta_pitch_pct: float = Field(18.0, ge=-50, le=50, description="Pitch raise for the Tamil voice")
    tts_girl_en_pitch_pct: float = Field(4.0, ge=-50, le=50, description="Pitch raise for the English child voice")
    tts_girl_rate: float = Field(1.06, ge=0.5, le=2.0)
    tts_girl_piper_semitones: float = Field(3.5, ge=0, le=7, description="Pitch raise for the offline Piper voices")
    tts_piper_pitch_method: Literal["wsola", "native"] = Field(
        "wsola", description="wsola: time-stretch, keeps the speaking speed exactly (voice A); native: Piper speaks "
                             "slower, then sped up (cleaner signal, but Piper stretches less than asked, so speech "
                             "comes out faster, especially Tamil)")
    tts_piper_noise_scale: float | None = Field(None, ge=0, le=1.5, description="Piper voice variation; lower = steadier")
    tts_piper_noise_w: float | None = Field(None, ge=0, le=1.5, description="Piper timing variation; lower = steadier")
    tts_rate: float = Field(1.1, ge=0.5, le=2.0)
    tts_pitch_pct: float = Field(5.0, ge=-50, le=50)
    tts_strategy: Literal["ta_only", "split", "auto"] = "auto"
    max_audio_upload_mb: float = Field(10.0, gt=0, le=50)

    # ---- Raspberry Pi devices ---------------------------------------------------
    device_discovery: bool = Field(True, description="Announce the server on the LAN so the Pi finds it "
                                   "(only when AIRA_ALLOW_LAN=true)")
    discovery_port: int = Field(8766, ge=1024, le=65535)

    # ---- Metrics --------------------------------------------------------------
    metrics_window: int = Field(200, ge=10, le=10000)

    @field_validator("llm_think", "agent_dry_run", mode="before")
    @classmethod
    def _think_default_words(cls, v):
        # AIRA_LLM_THINK= (empty), "default" or "none" means: don't send the flag at all.
        if isinstance(v, str) and v.strip().lower() in {"", "default", "none", "auto"}:
            return None
        return v

    @field_validator("ssl_certfile", "ssl_keyfile", mode="before")
    @classmethod
    def _blank_path_is_none(cls, v):
        # AIRA_SSL_CERTFILE= (empty) means plain HTTP, not "the current folder".
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _check_network_exposure(self) -> "Settings":
        if self.host not in LOOPBACK_HOSTS and not self.allow_lan:
            raise ValueError(
                f"AIRA_HOST={self.host!r} exposes the server beyond this laptop. "
                "Set AIRA_ALLOW_LAN=true to confirm (only on a trusted network, never the public internet)."
            )
        return self

    @property
    def effective_history_budget(self) -> int:
        """Token budget for system prompt + history + new message, leaving room for the reply."""
        return max(256, min(self.history_token_budget, self.llm_num_ctx - self.llm_max_tokens - 256))

    def validate_for_startup(self) -> None:
        """Checks that only matter when actually serving (tests may construct partial settings)."""
        key = self.api_key.get_secret_value()
        if len(key) < MIN_API_KEY_LENGTH:
            raise ValueError(
                f"AIRA_API_KEY must be at least {MIN_API_KEY_LENGTH} characters. "
                "Generate one with:  python scripts/generate_api_key.py"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()

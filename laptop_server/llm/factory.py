from __future__ import annotations

from ..config import Settings
from .base import LLMProvider
from .ollama_provider import OllamaProvider


def create_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.llm_model,
            num_ctx=settings.llm_num_ctx,
            temperature=settings.llm_temperature,
            top_p=settings.llm_top_p,
            max_tokens=settings.llm_max_tokens,
            keep_alive=settings.llm_keep_alive,
            think=settings.llm_think,
            timeout_s=settings.llm_request_timeout_s,
        )
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")

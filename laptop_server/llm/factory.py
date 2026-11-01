from __future__ import annotations

from ..config import Settings
from .base import LLMProvider
from .ollama_provider import OllamaProvider


def create_online(settings: Settings) -> LLMProvider:
    from .online_provider import OnlineProvider, resolve_base_url

    return OnlineProvider(base_url=resolve_base_url(settings.online_service or None, settings.online_base_url or None),
                          api_key=settings.online_api_key.get_secret_value(), model=settings.online_model,
                          temperature=settings.llm_temperature, top_p=settings.llm_top_p,
                          max_tokens=settings.llm_max_tokens, timeout_s=settings.online_timeout_s)


def create_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "online":
        return create_online(settings)
    if settings.llm_provider == "auto":
        from .online_provider import FallbackProvider

        return FallbackProvider(_ollama(settings), create_online(settings))
    if settings.llm_provider == "ollama":
        return _ollama(settings)
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider}")


def _ollama(settings: Settings) -> LLMProvider:
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

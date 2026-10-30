"""Provider-neutral LLM interface.

AIRA talks to the model only through :class:`LLMProvider`, so Ollama can later be
swapped for llama.cpp, LM Studio or a cloud API without touching the agent or API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": "..."}


class LLMError(Exception):
    """Base class for model-serving failures. ``code`` is stable and safe to show to clients."""

    code = "llm_error"
    http_status = 502

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"
    http_status = 503


class LLMModelNotFoundError(LLMError):
    code = "llm_model_not_found"
    http_status = 503


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    http_status = 504


@dataclass
class GenerationStats:
    """Timing and token counts for one generation. Times are seconds."""

    model: str
    total_s: float = 0.0              # server-reported wall time for the request
    load_s: float = 0.0               # time spent loading the model (0 when already warm)
    prompt_tokens: int = 0
    prompt_eval_s: float = 0.0
    completion_tokens: int = 0
    eval_s: float = 0.0
    ttft_s: float | None = None       # client-measured time to first visible token
    client_total_s: float = 0.0       # client-measured wall time
    done_reason: str | None = None

    @property
    def tokens_per_s(self) -> float:
        return self.completion_tokens / self.eval_s if self.eval_s > 0 else 0.0

    @property
    def prompt_tokens_per_s(self) -> float:
        return self.prompt_tokens / self.prompt_eval_s if self.prompt_eval_s > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tokens_per_s"] = round(self.tokens_per_s, 2)
        d["prompt_tokens_per_s"] = round(self.prompt_tokens_per_s, 2)
        for k, v in list(d.items()):
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


@dataclass
class StreamChunk:
    text: str
    done: bool = False
    stats: GenerationStats | None = None


@dataclass
class ChatResult:
    text: str
    stats: GenerationStats


@dataclass
class LoadedModel:
    name: str
    size_bytes: int
    size_vram_bytes: int

    @property
    def gpu_fraction(self) -> float:
        """Share of the model resident in VRAM. Below 1.0 means Ollama spilled layers to the CPU."""
        return self.size_vram_bytes / self.size_bytes if self.size_bytes else 0.0


@dataclass
class ProviderHealth:
    reachable: bool
    model: str
    model_available: bool = False
    server_version: str | None = None
    installed_models: list[str] = field(default_factory=list)
    loaded_models: list[LoadedModel] = field(default_factory=list)
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reachable": self.reachable,
            "model": self.model,
            "model_available": self.model_available,
            "server_version": self.server_version,
            "installed_models": self.installed_models,
            "loaded_models": [
                {
                    "name": m.name,
                    "size_gb": round(m.size_bytes / 1e9, 2),
                    "vram_gb": round(m.size_vram_bytes / 1e9, 2),
                    "gpu_percent": round(m.gpu_fraction * 100, 1),
                }
                for m in self.loaded_models
            ],
            "detail": self.detail,
        }


class LLMProvider(ABC):
    name: str = "abstract"
    model: str

    @abstractmethod
    def stream_chat(self, messages: list[Message]) -> AsyncIterator[StreamChunk]:
        """Yield text chunks; the last chunk has ``done=True`` and carries stats."""

    async def chat(self, messages: list[Message]) -> ChatResult:
        parts: list[str] = []
        stats: GenerationStats | None = None
        async for chunk in self.stream_chat(messages):
            parts.append(chunk.text)
            if chunk.done:
                stats = chunk.stats
        return ChatResult(text="".join(parts), stats=stats or GenerationStats(model=self.model))

    @abstractmethod
    async def health(self) -> ProviderHealth: ...

    async def warmup(self) -> float:
        """Load the model so the first real request is fast. Returns load seconds."""
        return 0.0

    async def aclose(self) -> None:
        return None

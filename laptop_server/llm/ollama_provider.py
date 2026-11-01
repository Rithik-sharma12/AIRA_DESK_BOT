"""Ollama implementation of :class:`LLMProvider` using Ollama's native REST API.

API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
Durations returned by Ollama are nanoseconds.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .base import (
    GenerationStats,
    LLMError,
    LLMModelNotFoundError,
    LLMProvider,
    LLMTimeoutError,
    LLMUnavailableError,
    LoadedModel,
    Message,
    ProviderHealth,
    StreamChunk,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger("aira.llm.ollama")

EMPTY_REPLY_FALLBACK = "Sorry, I got lost in thought there. Could you ask me that again?"


class ThinkUnsupportedError(LLMError):
    """The model does not accept Ollama's 'think' flag."""


def is_loopback_url(url: str) -> bool:
    """True when *url* points at this machine (so system proxies must be bypassed)."""
    host = (httpx.URL(url).host or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("127.")

NS = 1e9


def _model_matches(wanted: str, installed: str) -> bool:
    if wanted == installed:
        return True
    # "gemma4" is stored by Ollama as "gemma4:latest"
    return ":" not in wanted and installed == f"{wanted}:latest"


class OllamaProvider(LLMProvider):
    name = "ollama"
    supports_tools = True

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        num_ctx: int = 4096,
        temperature: float = 0.7,
        top_p: float = 0.95,
        max_tokens: int = 400,
        keep_alive: str = "30m",
        think: bool | None = None,
        timeout_s: float = 120.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        # "-1" = keep the model loaded indefinitely; Ollama wants that as the number -1
        self.keep_alive: str | int = -1 if str(keep_alive).strip() == "-1" else keep_alive
        self.think = think
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            transport=transport,
            # A Windows/VPN system proxy must never intercept calls to the local Ollama.
            trust_env=not is_loopback_url(self.base_url),
        )

    # ------------------------------------------------------------------ helpers
    def _options(self) -> dict[str, Any]:
        return {
            "num_ctx": self.num_ctx,
            "temperature": self.temperature,
            "top_p": self.top_p,
            "num_predict": self.max_tokens,
        }

    def build_chat_payload(self, messages: list[Message], *, stream: bool = True,
                           tools: list[ToolSpec] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": self._options(),
        }
        if tools:
            payload["tools"] = tools
        if self.think is not None:
            payload["think"] = self.think
        return payload

    @staticmethod
    def parse_tool_calls(raw: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for item in raw or []:
            fn = (item or {}).get("function") or {}
            name = fn.get("name")
            if not isinstance(name, str) or not name:
                continue
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some models return the arguments as a JSON string
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {}
            calls.append(ToolCall(name=name, arguments=args if isinstance(args, dict) else {}))
        return calls

    def _map_http_error(self, status: int, body: bytes) -> LLMError:
        try:
            message = json.loads(body).get("error", "")
        except (ValueError, AttributeError):
            message = body.decode("utf-8", "replace")[:300]
        lowered = message.lower()
        if status == 404 or ("not found" in lowered and "model" in lowered):
            return LLMModelNotFoundError(
                f"Model '{self.model}' is not installed in Ollama.",
                hint=f"Run:  ollama pull {self.model}   (or set AIRA_LLM_MODEL to an installed model)",
            )
        if "think" in lowered:
            return ThinkUnsupportedError(
                f"Ollama rejected the 'think' option for {self.model}: {message}",
                hint="Unset AIRA_LLM_THINK for this model.",
            )
        return LLMError(f"Ollama returned HTTP {status}: {message or 'no detail'}")

    @staticmethod
    def parse_stats(model: str, final: dict[str, Any], ttft_s: float | None, client_total_s: float) -> GenerationStats:
        return GenerationStats(
            model=final.get("model", model),
            total_s=final.get("total_duration", 0) / NS,
            load_s=final.get("load_duration", 0) / NS,
            prompt_tokens=int(final.get("prompt_eval_count", 0) or 0),
            prompt_eval_s=final.get("prompt_eval_duration", 0) / NS,
            completion_tokens=int(final.get("eval_count", 0) or 0),
            eval_s=final.get("eval_duration", 0) / NS,
            ttft_s=ttft_s,
            client_total_s=client_total_s,
            done_reason=final.get("done_reason"),
        )

    # ---------------------------------------------------------------- interface
    async def stream_chat(self, messages: list[Message],
                          tools: list[ToolSpec] | None = None) -> AsyncIterator[StreamChunk]:
        try:
            async for chunk in self._stream_chat_once(messages, tools):
                yield chunk
        except ThinkUnsupportedError:
            # Older/non-reasoning models (llama3.1, qwen2.5...) reject the flag. Nothing has been
            # yielded yet, so drop the flag for this model and retry transparently.
            log.warning("%s does not accept Ollama's 'think' flag; retrying without it.", self.model)
            self.think = None
            async for chunk in self._stream_chat_once(messages, tools):
                yield chunk

    async def _stream_chat_once(self, messages: list[Message],
                                tools: list[ToolSpec] | None = None) -> AsyncIterator[StreamChunk]:
        payload = self.build_chat_payload(messages, stream=True, tools=tools)
        t0 = time.perf_counter()
        ttft: float | None = None
        produced = False
        thinking_chars = 0
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    raise self._map_http_error(resp.status_code, await resp.aread())
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError:
                        log.warning("Skipping malformed line from Ollama: %.120s", line)
                        continue
                    if "error" in data:
                        raise LLMError(f"Ollama error: {data['error']}")
                    msg = data.get("message") or {}
                    text = msg.get("content") or ""
                    # msg["thinking"] (reasoning trace) is deliberately not spoken, only measured.
                    thinking_chars += len(msg.get("thinking") or "")
                    if text and ttft is None:
                        ttft = time.perf_counter() - t0
                    calls = self.parse_tool_calls(msg.get("tool_calls"))
                    produced = produced or bool(text.strip()) or bool(calls)
                    if calls and not data.get("done"):
                        yield StreamChunk(text=text, tool_calls=calls)
                        continue
                    if data.get("done"):
                        stats = self.parse_stats(self.model, data, ttft, time.perf_counter() - t0)
                        if not produced:
                            # Typical cause: a reasoning model spent the whole num_predict budget
                            # thinking. Say something rather than going silent.
                            log.warning(
                                "%s finished with no visible reply (done_reason=%s, thinking_chars=%d). "
                                "Set AIRA_LLM_THINK=false or raise AIRA_LLM_MAX_TOKENS.",
                                self.model, stats.done_reason, thinking_chars,
                            )
                            text = EMPTY_REPLY_FALLBACK
                        elif thinking_chars:
                            log.info("%s spent %d characters thinking before replying.", self.model, thinking_chars)
                        yield StreamChunk(text=text, done=True, stats=stats, tool_calls=calls)
                        return
                    if text:
                        yield StreamChunk(text=text)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url}.",
                hint="Start Ollama (it runs in the system tray on Windows) or check AIRA_OLLAMA_BASE_URL.",
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError(
                "Ollama did not respond in time.",
                hint="The model may still be loading, or it is spilling to CPU. Check `ollama ps`.",
            ) from exc
        # Stream ended without a done frame (Ollama crashed or connection dropped).
        raise LLMError("Ollama closed the stream before finishing the reply.")

    async def health(self) -> ProviderHealth:
        h = ProviderHealth(reachable=False, model=self.model)
        try:
            v = await self._client.get("/api/version")
            v.raise_for_status()
            h.reachable = True
            h.server_version = v.json().get("version")

            tags = await self._client.get("/api/tags")
            tags.raise_for_status()
            h.installed_models = sorted(m.get("name", "") for m in tags.json().get("models", []))
            h.model_available = any(_model_matches(self.model, n) for n in h.installed_models)

            ps = await self._client.get("/api/ps")
            if ps.status_code == 200:
                h.loaded_models = [
                    LoadedModel(
                        name=m.get("name", "?"),
                        size_bytes=int(m.get("size", 0) or 0),
                        size_vram_bytes=int(m.get("size_vram", 0) or 0),
                    )
                    for m in ps.json().get("models", [])
                ]
            if not h.model_available:
                h.detail = f"Model '{self.model}' not installed. Run: ollama pull {self.model}"
        except httpx.HTTPError as exc:
            h.detail = f"Ollama not reachable at {self.base_url}: {exc.__class__.__name__}"
        return h

    async def warmup(self) -> float:
        """Load the model with the same num_ctx used for chat (a different num_ctx forces a reload)."""
        t0 = time.perf_counter()
        try:
            r = await self._client.post(
                "/api/generate",
                json={"model": self.model, "keep_alive": self.keep_alive, "options": {"num_ctx": self.num_ctx}},
            )
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"Cannot reach Ollama at {self.base_url}.") from exc
        if r.status_code != 200:
            raise self._map_http_error(r.status_code, r.content)
        return time.perf_counter() - t0

    async def unload(self) -> None:
        """Ask Ollama to drop the model from memory (used by benchmarks for cold-start runs)."""
        await self._client.post("/api/generate", json={"model": self.model, "keep_alive": 0})

    async def aclose(self) -> None:
        await self._client.aclose()

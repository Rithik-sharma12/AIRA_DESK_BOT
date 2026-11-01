"""Online AI through any OpenAI-compatible chat API (with your own API key).

Works with OpenAI, Google Gemini, Groq, OpenRouter, DeepSeek, Mistral, Together and
Anthropic (all offer the same /chat/completions format), and with local servers such as
LM Studio or llama.cpp. Streaming and tool calling (AIRA's laptop skills) are supported.

Privacy: with this provider, what you say to AIRA (and the tool results, e.g. your laptop's
status) is sent to that company. The local Ollama provider keeps everything on the laptop.
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
    Message,
    ProviderHealth,
    StreamChunk,
    ToolCall,
    ToolSpec,
)

log = logging.getLogger("aira.llm.online")

# Base URLs of well-known services. Pick one with AIRA_ONLINE_SERVICE, or give AIRA_ONLINE_BASE_URL.
# The model name is always yours to choose (they change often): see the service's model list.
SERVICES: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "anthropic": "https://api.anthropic.com/v1",
}


class LLMAuthError(LLMError):
    code = "llm_auth"
    http_status = 502


def resolve_base_url(service: str | None, base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/")
    if service and service in SERVICES:
        return SERVICES[service]
    raise ValueError("Set AIRA_ONLINE_SERVICE (" + ", ".join(SERVICES) + ") or AIRA_ONLINE_BASE_URL")


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """AIRA's (Ollama-style) messages -> OpenAI format: tool calls get ids, results refer to them."""
    out: list[dict[str, Any]] = []
    waiting: list[str] = []
    n = 0
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = (c or {}).get("function") or {}
                cid = f"call_{n}"
                n += 1
                waiting.append(cid)
                args = fn.get("arguments") or {}
                calls.append({"id": cid, "type": "function",
                              "function": {"name": fn.get("name", ""),
                                           "arguments": args if isinstance(args, str) else json.dumps(args)}})
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif role == "tool":
            cid = waiting.pop(0) if waiting else f"call_{n}"
            out.append({"role": "tool", "tool_call_id": cid, "content": str(m.get("content", ""))})
        else:
            out.append({"role": role, "content": str(m.get("content", ""))})
    return out


class OnlineProvider(LLMProvider):
    name = "online"
    supports_tools = True

    def __init__(self, *, base_url: str, api_key: str, model: str, temperature: float = 0.7, top_p: float = 0.95,
                 max_tokens: int = 400, timeout_s: float = 60.0, transport: httpx.AsyncBaseTransport | None = None):
        if not model:
            raise ValueError("Set AIRA_ONLINE_MODEL (the model name from your AI service)")
        if not api_key:
            raise ValueError("Set AIRA_ONLINE_API_KEY (your API key / token for the AI service)")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature, self.top_p, self.max_tokens = temperature, top_p, max_tokens
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout_s, connect=8.0))

    def build_payload(self, messages: list[Message], tools: list[ToolSpec] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": self.model, "messages": to_openai_messages(messages), "stream": True,
                                   "temperature": self.temperature, "top_p": self.top_p,
                                   "max_tokens": self.max_tokens}
        if tools:
            payload["tools"] = tools
        return payload

    def _error(self, status: int, body: bytes) -> LLMError:
        try:
            detail = json.loads(body)
            detail = (detail.get("error") or {}).get("message") if isinstance(detail.get("error"), dict) \
                else detail.get("error") or detail.get("message") or body[:200].decode("utf-8", "replace")
        except ValueError:
            detail = body[:200].decode("utf-8", "replace")
        if status in (401, 403):
            return LLMAuthError(f"The online AI refused the API key ({status}).",
                                hint="Check AIRA_ONLINE_API_KEY (and that it is for this service).")
        if status == 404:
            return LLMModelNotFoundError(f"Online model '{self.model}' not found: {detail}",
                                         hint="Check AIRA_ONLINE_MODEL against the service's model list.")
        if status == 429:
            return LLMUnavailableError(f"Online AI rate limit or quota reached: {detail}",
                                       hint="Wait a minute, or check the plan / billing of your API key.")
        return LLMError(f"Online AI error {status}: {detail}")

    async def stream_chat(self, messages: list[Message],
                          tools: list[ToolSpec] | None = None) -> AsyncIterator[StreamChunk]:
        t0 = time.perf_counter()
        ttft: float | None = None
        parts: dict[int, dict[str, str]] = {}            # streamed tool calls, by index
        chars, usage, finish = 0, None, None
        try:
            async with self._client.stream("POST", "/chat/completions",
                                           json=self.build_payload(messages, tools)) as resp:
                if resp.status_code != 200:
                    raise self._error(resp.status_code, await resp.aread())
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        raise LLMError(f"Online AI error: {obj['error']}")
                    usage = obj.get("usage") or usage
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or {}
                        finish = choice.get("finish_reason") or finish
                        for tc in delta.get("tool_calls") or []:
                            slot = parts.setdefault(int(tc.get("index", len(parts))), {"name": "", "args": ""})
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                        text = delta.get("content") or ""
                        if text:
                            if ttft is None:
                                ttft = time.perf_counter() - t0
                            chars += len(text)
                            yield StreamChunk(text=text)
        except httpx.ConnectError as exc:
            raise LLMUnavailableError(f"Cannot reach the online AI at {self.base_url} (no internet?).") from exc
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("The online AI did not answer in time.") from exc
        calls = []
        for _, slot in sorted(parts.items()):
            try:
                args = json.loads(slot["args"]) if slot["args"].strip() else {}
            except ValueError:
                args = {}
            if slot["name"]:
                calls.append(ToolCall(name=slot["name"], arguments=args if isinstance(args, dict) else {}))
        total = time.perf_counter() - t0
        stats = GenerationStats(model=self.model, ttft_s=ttft, client_total_s=total, done_reason=finish,
                                prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
                                completion_tokens=int((usage or {}).get("completion_tokens") or max(1, chars // 4)),
                                eval_s=max(1e-3, total - (ttft or 0.0)))
        yield StreamChunk(text="", done=True, stats=stats, tool_calls=calls)

    async def health(self) -> ProviderHealth:
        h = ProviderHealth(reachable=False, model=self.model)
        try:
            r = await self._client.get("/models")
        except httpx.HTTPError as exc:
            h.detail = f"Online AI not reachable at {self.base_url}: {exc.__class__.__name__}"
            return h
        if r.status_code in (401, 403):
            h.detail = "Online AI refused the API key"
            return h
        h.reachable = True
        h.model_available = True                      # some services do not list every model; trust the name
        if r.status_code == 200:
            try:
                names = [str(m.get("id", "")) for m in r.json().get("data", [])]
            except ValueError:
                names = []
            h.installed_models = sorted(names)[:200]
            if names and not any(n == self.model or n.endswith("/" + self.model) for n in names):
                h.detail = f"'{self.model}' is not in the service's model list (it may still work)"
        h.server_version = self.base_url
        return h

    async def warmup(self) -> float:
        return 0.0                                    # nothing to load

    async def aclose(self) -> None:
        await self._client.aclose()


class FallbackProvider(LLMProvider):
    """Local first, online when the local model is unreachable (or the other way round).

    If the first provider fails before saying anything, the second one answers instead and
    is used straight away for the next ``cooldown_s`` seconds; then the first is tried again.
    """

    def __init__(self, first: LLMProvider, second: LLMProvider, cooldown_s: float = 60.0, clock=time.monotonic):
        self.first, self.second = first, second
        self.cooldown_s, self._clock = cooldown_s, clock
        self._first_down_until = 0.0
        self.supports_tools = first.supports_tools and second.supports_tools
        self.name = f"{first.name}+{second.name}"
        self.last_used = first.name

    @property
    def model(self) -> str:  # type: ignore[override]
        active = self.second if self._clock() < self._first_down_until else self.first
        return active.model

    async def stream_chat(self, messages: list[Message],
                          tools: list[ToolSpec] | None = None) -> AsyncIterator[StreamChunk]:
        if self._clock() >= self._first_down_until:
            started = False
            try:
                async for chunk in self.first.stream_chat(messages, tools):
                    started = True
                    yield chunk
                self.last_used = self.first.name
                return
            except (LLMUnavailableError, LLMModelNotFoundError, LLMTimeoutError) as exc:
                if started:
                    raise
                self._first_down_until = self._clock() + self.cooldown_s
                log.warning("%s unavailable (%s); answering with %s", self.first.name, exc, self.second.name)
        async for chunk in self.second.stream_chat(messages, tools):
            yield chunk
        self.last_used = self.second.name

    async def health(self) -> ProviderHealth:
        h1 = await self.first.health()
        if h1.reachable and h1.model_available:
            return h1
        h2 = await self.second.health()
        h2.detail = f"{self.first.name}: {h1.detail or 'unavailable'}; using {self.second.name}" + \
            (f" ({h2.detail})" if h2.detail else "")
        return h2

    async def warmup(self) -> float:
        try:
            return await self.first.warmup()
        except LLMError:
            return 0.0

    async def aclose(self) -> None:
        await self.first.aclose()
        await self.second.aclose()

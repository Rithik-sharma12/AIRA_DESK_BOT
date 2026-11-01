"""AIRA on the Pi without the laptop: an online AI (your API key) and a chat page for your phone.

Brain modes (config "brain"):
    laptop   (default) AIRA's brain is the laptop; the chat page sends your words there.
    online   always ask the online AI directly from the Pi.
    auto     the laptop when it is connected, otherwise the online AI.

The chat page runs on the Pi at http://<pi-ip>:8770/ (phone or laptop browser on the same
hotspot). It asks for the 6-digit code shown on AIRA's System screen once, then remembers it.
The browser speaks the answers (AIRA Half has no speaker); the Pi shows them.

Only the Python standard library is used (urllib, asyncio), so nothing new is installed on
the Pi. The API key stays in ~/.config/aira-pi/config.json (readable only by you) and is never
printed or shown on the page.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

log = logging.getLogger("aira_pi.online")

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

PERSONA = (
    "You are AIRA, a cheerful, kind desk companion with the voice of a sweet 12-year-old girl. "
    "You live on a small Raspberry Pi screen. Reply in the user's language: Tamil (in Tamil script), "
    "English, or Tanglish, matching how they wrote. Keep replies short (one to three sentences) and "
    "warm. No markdown, lists or emoji. If asked to control the laptop, say the laptop is not connected."
)


class OnlineError(Exception):
    """A problem with the online AI, worded for the user."""


def base_url_for(service: str | None, base_url: str | None) -> str:
    if base_url:
        return base_url.rstrip("/")
    if service in SERVICES:
        return SERVICES[service]
    raise OnlineError("No online AI set up. On AIRA run:  python3 -m aira_pi online setup")


class OnlineBrain:
    """Chat with an OpenAI-compatible API, streaming, with a short memory of the conversation."""

    def __init__(self, base_url: str, api_key: str, model: str, *, max_tokens: int = 300, timeout_s: float = 40,
                 history_turns: int = 8, opener=urllib.request.urlopen, clock=time.localtime):
        if not (api_key and model):
            raise OnlineError("The online AI needs a model name and an API key (python3 -m aira_pi online setup)")
        self.base_url, self.model = base_url.rstrip("/"), model
        self._key = api_key
        self.max_tokens, self.timeout_s = max_tokens, timeout_s
        self.history: deque[dict] = deque(maxlen=history_turns * 2)
        self._open, self._clock = opener, clock

    def messages(self, text: str) -> list[dict]:
        now = time.strftime("%A %d %B %Y, %H:%M", self._clock())
        return [{"role": "system", "content": f"{PERSONA} It is now {now}."}, *self.history,
                {"role": "user", "content": text}]

    def stream(self, text: str) -> Iterator[str]:
        """Blocking: yields the reply in pieces (run it in a thread)."""
        body = json.dumps({"model": self.model, "messages": self.messages(text), "stream": True,
                           "max_tokens": self.max_tokens, "temperature": 0.7}).encode()
        req = urllib.request.Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
            "Authorization": f"Bearer {self._key}", "Content-Type": "application/json", "Accept": "text/event-stream"})
        parts: list[str] = []
        try:
            with self._open(req, timeout=self.timeout_s) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    for choice in obj.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content") or ""
                        if piece:
                            parts.append(piece)
                            yield piece
        except urllib.error.HTTPError as exc:
            raise OnlineError(_http_message(exc.code, self.model)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OnlineError("Cannot reach the online AI. Is the hotspot connected to the internet?") from exc
        reply = "".join(parts).strip()
        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": reply})

    def ask(self, text: str) -> str:
        return "".join(self.stream(text)).strip()


def _http_message(status: int, model: str) -> str:
    if status in (401, 403):
        return "The online AI refused the API key. Check it with: python3 -m aira_pi online setup"
    if status == 404:
        return f"The online AI does not know the model '{model}'. Check the model name."
    if status == 429:
        return "The online AI says: too many requests or no quota left. Try again in a minute."
    return f"The online AI answered with error {status}."


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


# =========================================================================== chat page
class RateLimit:
    def __init__(self, per_minute: int = 20, clock=time.monotonic):
        self.per_minute, self.clock = per_minute, clock
        self.times: deque[float] = deque()

    def allow(self) -> bool:
        now = self.clock()
        while self.times and now - self.times[0] > 60:
            self.times.popleft()
        if len(self.times) >= self.per_minute:
            return False
        self.times.append(now)
        return True


class ChatServer:
    """A tiny HTTP server: GET / is the chat page, POST /ask {code, text} -> {reply, via}."""

    MAX_BODY = 4096

    def __init__(self, ask: Callable[[str], Awaitable[tuple[str, str]]], code: Callable[[], str], port: int = 8770):
        self.ask, self.code, self.port = ask, code, port
        self.limit = RateLimit()
        self.failures = 0
        self._server: asyncio.base_events.Server | None = None

    async def start(self, host: str = "0.0.0.0") -> None:
        self._server = await asyncio.start_server(self.handle, host, self.port)
        log.info("Chat page on port %d", self.port)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = (lines[0].split(" ") + ["", "", ""])[:3]
            headers = {k.strip().lower(): v.strip() for k, _, v in (ln.partition(":") for ln in lines[1:] if ln)}
            if method == "GET" and path in ("/", "/index.html"):
                await self._send(writer, 200, PAGE.encode(), "text/html; charset=utf-8")
            elif method == "POST" and path == "/ask":
                size = int(headers.get("content-length", "0") or 0)
                if size > self.MAX_BODY:
                    await self._json(writer, 413, {"error": "message too long"})
                    return
                body = await asyncio.wait_for(reader.readexactly(size), timeout=10)
                await self._ask(writer, body)
            else:
                await self._send(writer, 404, b"not found")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError, ValueError,
                ConnectionError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _ask(self, writer, body: bytes) -> None:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            await self._json(writer, 400, {"error": "bad request"})
            return
        if self.failures >= 10:                             # someone is guessing codes
            await self._json(writer, 429, {"error": "too many wrong codes; restart AIRA to reset"})
            return
        if not secrets.compare_digest(str(data.get("code", "")), self.code()):
            self.failures += 1
            await self._json(writer, 401, {"error": "wrong code: see AIRA's System screen"})
            return
        self.failures = 0
        text = " ".join(str(data.get("text", "")).split())[:500]
        if not text:
            await self._json(writer, 200, {"ok": True})     # code check only
            return
        if not self.limit.allow():
            await self._json(writer, 429, {"error": "slow down a little"})
            return
        try:
            reply, via = await self.ask(text)
            await self._json(writer, 200, {"reply": reply, "via": via})
        except OnlineError as exc:
            await self._json(writer, 503, {"error": str(exc)})

    async def _json(self, writer, status: int, obj: dict) -> None:
        await self._send(writer, status, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    @staticmethod
    async def _send(writer, status: int, body: bytes, ctype: str = "text/plain") -> None:
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 413: "Too Large",
                  429: "Too Many Requests", 503: "Unavailable"}.get(status, "OK")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                     "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n"
                     .encode() + body)
        await writer.drain()


PAGE = (Path(__file__).with_name("chat_page.html")).read_text(encoding="utf-8") \
    if (Path(__file__).with_name("chat_page.html")).exists() else "<p>chat page missing</p>"

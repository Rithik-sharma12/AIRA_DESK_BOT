"""AIRA's agent engine: conversation + task automation.

For each user message:

1. If AIRA asked "shall I shut down?" last turn, a yes/no answer settles it.
   Risky actions are confirmed here, in code, never by the model.
2. Instant commands ("volume up", "open chrome") run straight away, no model needed.
3. Otherwise the model answers, and may call tools. Action tools already say what they
   did, so AIRA speaks that directly; information tools (status, time, reminders) go
   back to the model once so it can answer the actual question.

``run`` yields :class:`AgentEvent` items that the HTTP and device layers turn into
SSE events / WebSocket frames. Captions and face states are also published on the
event hub, so the Pi mirrors every conversation.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..events import EventHub
from ..llm.base import GenerationStats, LLMError, LLMProvider, Message, ToolCall
from .conversation import ConversationManager, Session
from .router import FastRouter, confirmation_answer, detect_lang
from .tools.base import ToolRegistry, ToolResult, t

log = logging.getLogger("aira.agent")
CONFIRM_TIMEOUT_S = 60.0


@dataclass
class AgentEvent:
    kind: str                       # token | tool | confirm | done | error
    data: dict[str, Any] = field(default_factory=dict)
    exc: Exception | None = None    # the original error, for HTTP status mapping


@dataclass
class PendingAction:
    tool: str
    args: dict[str, Any]
    prompt: str
    created: float


class AgentEngine:
    def __init__(self, provider: LLMProvider, conversations: ConversationManager, *,
                 tools: ToolRegistry | None, router: FastRouter | None, hub: EventHub | None = None,
                 max_rounds: int = 3, fast_commands: bool = True):
        self.provider = provider
        self.conversations = conversations
        self.tools = tools
        self.router = router if fast_commands else None
        self.hub = hub
        self.max_rounds = max_rounds
        self._pending: dict[str, PendingAction] = {}

    @property
    def tools_enabled(self) -> bool:
        return self.tools is not None and getattr(self.provider, "supports_tools", False)

    # ------------------------------------------------------------------ API
    async def run(self, session: Session, system_prompt: str, text: str,
                  source: str = "web") -> AsyncIterator[AgentEvent]:
        """Run one user turn. ``source`` says where it came from ("web", or a device name such as
        "AIRA Pi"), so a web page can show and speak conversations started on the Pi's touch screen."""
        self._publish({"type": "caption", "role": "user", "text": text[:300], "source": source})
        async for ev in self._run(session, system_prompt, text):
            if ev.kind == "done":
                reply = ev.data["reply"]
                self._publish({"type": "caption", "role": "aira", "text": reply[:300], "source": source})
                if self.hub:
                    # Roughly how long speaking takes; a device with no audio returns to idle after this.
                    self.hub.set_state("speaking" if reply else "idle",
                                       estimate_s=round(min(30.0, len(reply) / 14 + 0.5), 1))
            yield ev

    async def _run(self, session: Session, system_prompt: str, text: str) -> AsyncIterator[AgentEvent]:
        lang = detect_lang(text)
        self._state("thinking")
        t0 = time.perf_counter()
        try:
            # 1. answer to a pending confirmation
            pending = self._pending.pop(session.id, None)
            if pending and time.time() - pending.created < CONFIRM_TIMEOUT_S:
                answer = confirmation_answer(text)
                if answer is True:
                    result = await self.tools.run(pending.tool, pending.args, lang)  # type: ignore[union-attr]
                    async for ev in self._finish_direct(session, text, result.say, t0, tool=pending.tool, result=result):
                        yield ev
                    return
                if answer is False:
                    async for ev in self._finish_direct(session, text, t(lang, "Okay, I won't.", "சரி, செய்யவில்லை."), t0):
                        yield ev
                    return
                # anything else: drop the pending action and treat this as a new request

            # 2. instant commands
            if self.tools is not None and self.router is not None:
                route = self.router.match(text)
                if route and self.tools.get(route.tool):
                    tool = self.tools.get(route.tool)
                    if tool.risk == "confirm":  # type: ignore[union-attr]
                        async for ev in self._ask_confirmation(session, text, route.tool, route.args, lang, t0):
                            yield ev
                        return
                    result = await self.tools.run(route.tool, route.args, lang)
                    async for ev in self._finish_direct(session, text, result.say, t0, tool=route.tool, result=result,
                                                        fast=True):
                        yield ev
                    return

            # 3. the model, with tools
            async for ev in self._model_turn(session, system_prompt, text, lang, t0):
                yield ev
        except LLMError as exc:
            self._state("error")
            yield AgentEvent("error", {"code": exc.code, "message": str(exc), "hint": exc.hint}, exc=exc)

    # ------------------------------------------------------------- internals
    async def _model_turn(self, session: Session, system_prompt: str, text: str, lang: str,
                          t0: float) -> AsyncIterator[AgentEvent]:
        messages: list[Message] = self.conversations.build_messages(session, system_prompt, text)
        specs = self.tools.specs() if self.tools_enabled else None  # type: ignore[union-attr]
        spoken: list[str] = []
        stats: GenerationStats | None = None
        first_stats: GenerationStats | None = None
        for _round in range(self.max_rounds):
            calls: list[ToolCall] = []
            round_text: list[str] = []
            async for chunk in self.provider.stream_chat(messages, tools=specs) if specs else \
                    self.provider.stream_chat(messages):
                if chunk.text:
                    round_text.append(chunk.text)
                    spoken.append(chunk.text)
                    yield AgentEvent("token", {"text": chunk.text})
                calls.extend(chunk.tool_calls)
                if chunk.done:
                    stats = chunk.stats
                    first_stats = first_stats or stats
            if not calls:
                break
            messages.append({"role": "assistant", "content": "".join(round_text),
                             "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in calls]})
            need_model = False
            for call in calls[:4]:
                tool = self.tools.get(call.name)  # type: ignore[union-attr]
                if tool is not None and tool.risk == "confirm":
                    async for ev in self._ask_confirmation(session, text, call.name, call.arguments, lang, t0,
                                                           prefix="".join(spoken)):
                        yield ev
                    return
                result = await self.tools.run(call.name, call.arguments, lang)  # type: ignore[union-attr]
                yield AgentEvent("tool", {"name": call.name, "ok": result.ok, "say": result.say})
                messages.append({"role": "tool", "tool_name": call.name,
                                 "content": json.dumps(result.for_model(), ensure_ascii=False)})
                if tool is None or tool.kind == "info" or not result.ok:
                    need_model = True
                else:
                    sentence = (" " if spoken and not "".join(spoken).endswith(" ") else "") + result.say
                    spoken.append(sentence)
                    yield AgentEvent("token", {"text": sentence})
            if not need_model:
                break
        reply = "".join(spoken).strip()
        if not reply:
            reply = t(lang, "Sorry, I'm not sure how to help with that.", "மன்னிக்கவும், அது எனக்குப் புரியவில்லை.")
            yield AgentEvent("token", {"text": reply})
        self.conversations.record_exchange(session, text, reply)
        final = stats or GenerationStats(model=getattr(self.provider, "model", "?"))
        if first_stats and first_stats is not final:
            final.ttft_s = first_stats.ttft_s  # first visible word of the whole turn
        final.client_total_s = time.perf_counter() - t0
        yield self._done(session, reply, final)

    async def _ask_confirmation(self, session: Session, text: str, tool_name: str, args: dict[str, Any],
                                lang: str, t0: float, prefix: str = "") -> AsyncIterator[AgentEvent]:
        tool = self.tools.get(tool_name)  # type: ignore[union-attr]
        prompt = tool.prompt_for_confirmation(args, lang)  # type: ignore[union-attr]
        self._pending[session.id] = PendingAction(tool_name, args, prompt, time.time())
        sentence = (" " if prefix and not prefix.endswith(" ") else "") + prompt
        yield AgentEvent("token", {"text": sentence})
        yield AgentEvent("confirm", {"tool": tool_name, "prompt": prompt})
        self._publish({"type": "confirm", "prompt": prompt})
        reply = (prefix + sentence).strip()
        self.conversations.record_exchange(session, text, reply)
        yield self._done(session, reply, GenerationStats(model="aira-agent", ttft_s=time.perf_counter() - t0,
                                                         client_total_s=time.perf_counter() - t0))

    async def _finish_direct(self, session: Session, text: str, say: str, t0: float, *, tool: str | None = None,
                             result: ToolResult | None = None, fast: bool = False) -> AsyncIterator[AgentEvent]:
        if tool and result is not None:
            yield AgentEvent("tool", {"name": tool, "ok": result.ok, "say": result.say, "fast": fast})
        yield AgentEvent("token", {"text": say})
        self.conversations.record_exchange(session, text, say)
        elapsed = time.perf_counter() - t0
        yield self._done(session, say, GenerationStats(model="aira-fast" if fast else "aira-agent",
                                                       ttft_s=elapsed, client_total_s=elapsed))

    def _done(self, session: Session, reply: str, stats: GenerationStats) -> AgentEvent:
        return AgentEvent("done", {"session_id": session.id, "reply": reply, "stats": stats})

    def pending_for(self, session_id: str) -> PendingAction | None:
        return self._pending.get(session_id)

    def _publish(self, event: dict[str, Any]) -> None:
        if self.hub:
            self.hub.publish(event)

    def _state(self, state: str) -> None:
        if self.hub:
            self.hub.set_state(state)

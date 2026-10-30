"""Short-term conversation memory (Phase 1).

Sessions live in RAM and expire after inactivity. Long-term, consent-based memory in
SQLite arrives in Phase 7 and will sit behind the same ``ConversationManager`` API.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field

from ..llm.base import Message

_TAMIL_RE = re.compile(r"[஀-௿]")


def estimate_tokens(text: str) -> int:
    """Conservative token estimate used only for trimming history.

    Latin text averages roughly 4 characters per token. Tamil script tokenizes far
    less efficiently in most LLM vocabularies, so each Tamil character is counted as
    one token. Exact counts come back from Ollama (prompt_eval_count) and are logged,
    so this heuristic can be calibrated against real numbers on your hardware.
    """
    tamil = len(_TAMIL_RE.findall(text))
    other = len(text) - tamil
    return tamil + other // 4 + 4  # +4 for role/formatting overhead


@dataclass
class Session:
    id: str
    created: float
    last_active: float
    messages: list[Message] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ConversationManager:
    def __init__(
        self,
        *,
        max_turns: int = 12,
        token_budget: int = 3000,
        ttl_s: float = 3600,
        max_sessions: int = 50,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.max_turns = max_turns
        self.token_budget = token_budget
        self.ttl_s = ttl_s
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()

    # ------------------------------------------------------------- sessions
    def __len__(self) -> int:
        return len(self._sessions)

    def get_or_create(self, session_id: str | None) -> Session:
        self._evict()
        now = self._clock()
        if session_id and session_id in self._sessions:
            s = self._sessions[session_id]
            s.last_active = now
            self._sessions.move_to_end(session_id)
            return s
        sid = session_id or uuid.uuid4().hex
        s = Session(id=sid, created=now, last_active=now)
        self._sessions[sid] = s
        self._evict()
        return s

    def reset(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def _evict(self) -> None:
        now = self._clock()
        expired = [sid for sid, s in self._sessions.items() if now - s.last_active > self.ttl_s]
        for sid in expired:
            del self._sessions[sid]
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)  # least recently used

    # --------------------------------------------------------------- prompts
    def build_messages(self, session: Session, system_prompt: str, user_text: str) -> list[Message]:
        """System prompt + as much recent history as fits the budget + the new user message."""
        history = session.messages[-self.max_turns * 2 :]
        fixed = estimate_tokens(system_prompt) + estimate_tokens(user_text)
        # Drop the oldest user/assistant pair until everything fits.
        while history and fixed + sum(estimate_tokens(m["content"]) for m in history) > self.token_budget:
            history = history[2:] if len(history) >= 2 else []
        return [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_text},
        ]

    def record_exchange(self, session: Session, user_text: str, assistant_text: str) -> None:
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": assistant_text})
        # Keep RAM bounded; prompts are trimmed further in build_messages.
        cap = self.max_turns * 2
        if len(session.messages) > cap:
            session.messages = session.messages[-cap:]
        session.last_active = self._clock()

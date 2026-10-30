from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, description="What the user said or typed")
    session_id: str | None = Field(default=None, pattern=SESSION_ID_PATTERN)


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    stats: dict[str, Any]


class ErrorResponse(BaseModel):
    code: str
    message: str
    hint: str | None = None


class HealthResponse(BaseModel):
    status: str
    version: str


class StatusResponse(BaseModel):
    status: str
    version: str
    uptime_s: float
    provider: str
    llm: dict[str, Any]
    active_sessions: int
    config: dict[str, Any]

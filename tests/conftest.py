from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laptop_server.app import create_app  # noqa: E402
from laptop_server.config import Settings  # noqa: E402
from laptop_server.llm.base import (  # noqa: E402
    GenerationStats,
    LLMError,
    LLMProvider,
    Message,
    ProviderHealth,
    StreamChunk,
)

TEST_KEY = "test-key-" + "x" * 40


class FakeProvider(LLMProvider):
    """Deterministic provider for API tests. Records every prompt it receives."""

    name = "fake"

    def __init__(self, reply: str = "Hello from AIRA!", error: LLMError | None = None, model: str = "fake-model"):
        self.model = model
        self.reply = reply
        self.error = error
        self.calls: list[list[Message]] = []
        self.warmed = False
        self.closed = False

    async def stream_chat(self, messages: list[Message]) -> AsyncIterator[StreamChunk]:
        self.calls.append(messages)
        if self.error:
            raise self.error
        words = self.reply.split(" ")
        for i, w in enumerate(words):
            yield StreamChunk(text=w if i == 0 else " " + w)
        yield StreamChunk(
            text="",
            done=True,
            stats=GenerationStats(model=self.model, completion_tokens=len(words), eval_s=0.1, ttft_s=0.05),
        )

    async def health(self) -> ProviderHealth:
        return ProviderHealth(reachable=True, model=self.model, model_available=True, server_version="fake")

    async def warmup(self) -> float:
        self.warmed = True
        return 0.0

    async def aclose(self) -> None:
        self.closed = True


def make_settings(**overrides) -> Settings:
    base = dict(api_key=SecretStr(TEST_KEY), log_dir=None, llm_warmup_on_start=True, _env_file=None)
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def client(fake_provider):
    app = create_app(make_settings(), provider=fake_provider)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_KEY}"}

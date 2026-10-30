"""Live tests against your real Ollama + Gemma model. Run on the HP Omen:

    $env:AIRA_LIVE_TESTS = "1"; python -m pytest tests/integration/test_live_ollama.py -v -s

They check that the model answers in English, Tamil and Tanglish and print timings.
Language checks are deliberately loose: they confirm the right *script* came back,
not that the answer is good. Judge quality by listening/reading.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest

from laptop_server.agent.persona import build_system_prompt
from laptop_server.config import Settings
from laptop_server.llm.factory import create_provider

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("AIRA_LIVE_TESTS") != "1", reason="set AIRA_LIVE_TESTS=1 to run"),
]

TAMIL = re.compile(r"[஀-௿]")

CASES = [
    ("english", "Hey AIRA, good morning! Introduce yourself in one sentence.", None),
    ("tamil", "வணக்கம் AIRA! இன்னைக்கு என்ன plan?", True),
    ("tanglish", "Enna AIRA, innikku enna plan? Java study pannanum.", None),
    ("honesty", "What is my CPU temperature right now?", None),
]


@pytest.mark.parametrize("name,prompt,expect_tamil", CASES)
def test_live_reply(name, prompt, expect_tamil):
    s = Settings()
    provider = create_provider(s)
    system = build_system_prompt(user_name=s.user_name, tamil_output_script=s.tamil_output_script)

    async def go():
        try:
            return await provider.chat([{"role": "system", "content": system}, {"role": "user", "content": prompt}])
        finally:
            await provider.aclose()

    res = asyncio.run(go())
    st = res.stats
    print(f"\n[{name}] ttft={st.ttft_s:.2f}s tok/s={st.tokens_per_s:.1f} load={st.load_s:.1f}s\n  -> {res.text}")
    assert res.text.strip()
    if expect_tamil:
        assert TAMIL.search(res.text), "expected Tamil script in reply"
    if name == "honesty":
        assert not re.search(r"\b\d{2}\s?(°|degrees)", res.text), "model invented a temperature reading"

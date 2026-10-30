from datetime import datetime

import pytest
from pydantic import SecretStr, ValidationError

from laptop_server.agent.persona import build_system_prompt
from laptop_server.app import create_app
from laptop_server.config import Settings
from tests.conftest import FakeProvider, make_settings


def test_defaults_are_local_only():
    s = Settings(_env_file=None)
    assert s.host == "127.0.0.1"
    assert s.ollama_base_url == "http://127.0.0.1:11434"
    assert s.log_conversation_content is False


def test_lan_bind_requires_explicit_opt_in():
    with pytest.raises(ValidationError, match="ALLOW_LAN"):
        Settings(host="0.0.0.0", _env_file=None)
    assert Settings(host="0.0.0.0", allow_lan=True, _env_file=None).host == "0.0.0.0"


def test_short_api_key_refused_at_startup():
    with pytest.raises(ValueError, match="at least 32"):
        create_app(make_settings(api_key=SecretStr("short")), provider=FakeProvider())
    with pytest.raises(ValueError):
        create_app(make_settings(api_key=SecretStr("")), provider=FakeProvider())


def test_env_override(monkeypatch):
    monkeypatch.setenv("AIRA_LLM_MODEL", "gemma4:12b-it-qat")
    monkeypatch.setenv("AIRA_LLM_THINK", "false")
    s = Settings(_env_file=None)
    assert s.llm_model == "gemma4:12b-it-qat"
    assert s.llm_think is False


def test_history_budget_leaves_room_for_reply():
    s = Settings(llm_num_ctx=4096, llm_max_tokens=400, history_token_budget=99999, _env_file=None)
    assert s.effective_history_budget == 4096 - 400 - 256


def test_persona_mentions_limits_and_script():
    p = build_system_prompt(user_name="Raj", tamil_output_script="tamil", now=datetime(2026, 10, 30, 18, 0))
    assert "AIRA" in p
    assert "Raj's desk" in p
    assert "never make up" in p.lower()
    assert "Tamil script" in p
    assert "Friday, 30 October 2026" in p
    latin = build_system_prompt(tamil_output_script="latin")
    assert "Tanglish" in latin and "your user's desk" in latin


def test_custom_persona_file(tmp_path):
    f = tmp_path / "persona.txt"
    f.write_text("You are AIRA. Today is {today}. JSON example: {not_a_key}", encoding="utf-8")
    p = build_system_prompt(persona_file=f)
    assert p.startswith("You are AIRA.")  # unknown placeholder -> used verbatim, no crash

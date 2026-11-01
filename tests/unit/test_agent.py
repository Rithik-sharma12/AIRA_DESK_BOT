"""Task automation: instant commands, confirmations, model tool calls, reminders."""

import asyncio
import json
import time
from collections.abc import AsyncIterator

import pytest

from laptop_server.agent.conversation import ConversationManager
from laptop_server.agent.engine import AgentEngine
from laptop_server.agent.router import FastRouter, confirmation_answer
from laptop_server.agent.scheduler import Scheduler
from laptop_server.agent.tools import DryRunBackend, create_toolkit
from laptop_server.agent.tools.apps import AppCatalog, clean_url
from laptop_server.agent.tools.base import ToolArgumentError, validate_args
from laptop_server.agent.tools.builtin import seconds_until, status_sentence
from laptop_server.events import EventHub
from laptop_server.llm.base import GenerationStats, LLMProvider, ProviderHealth, StreamChunk, ToolCall


def run(coro):
    return asyncio.run(coro)


class ScriptedProvider(LLMProvider):
    """Each call to stream_chat plays the next scripted round: text and/or tool calls."""

    name = "scripted"
    supports_tools = True
    model = "scripted"

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.calls = []

    async def stream_chat(self, messages, tools=None) -> AsyncIterator[StreamChunk]:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        text, calls = self.rounds.pop(0) if self.rounds else ("", [])
        if text:
            yield StreamChunk(text=text)
        yield StreamChunk(text="", done=True, tool_calls=[ToolCall(n, a) for n, a in calls],
                          stats=GenerationStats(model="scripted", completion_tokens=5, eval_s=0.1, ttft_s=0.05))

    async def health(self):
        return ProviderHealth(reachable=True, model="scripted", model_available=True)


def make_engine(tmp_path, rounds=()):
    hub = EventHub()
    backend = DryRunBackend()
    kit = create_toolkit(hub=hub, data_dir=tmp_path, apps_file=None, dry_run=True, backend=backend)
    provider = ScriptedProvider(rounds)
    convs = ConversationManager()
    engine = AgentEngine(provider, convs, tools=kit.registry, router=FastRouter(kit.catalog), hub=hub)
    return engine, provider, backend, convs, hub


async def say(engine, convs, text, sid="s1"):
    session = convs.get_or_create(sid)
    events = [ev async for ev in engine.run(session, "system", text)]
    reply = next(ev.data["reply"] for ev in events if ev.kind == "done")
    return events, reply


# --------------------------------------------------------------------- router
@pytest.mark.parametrize("text,tool,args", [
    ("Volume up", "volume", {"action": "up"}),
    ("please turn the volume down", "volume", {"action": "down"}),
    ("volume ah increase pannu", "volume", {"action": "up"}),
    ("சத்தம் கூட்டு", "volume", {"action": "up"}),
    ("set volume to 40%", "volume", {"action": "set", "level": 40}),
    ("mute", "volume", {"action": "mute"}),
    ("pause the music", "media_control", {"action": "play_pause"}),
    ("next song", "media_control", {"action": "next"}),
    ("open chrome", "open_app", {"name": "chrome"}),
    ("Open VS Code please", "open_app", {"name": "vscode"}),
    ("chrome open pannu", "open_app", {"name": "chrome"}),
    ("open youtube", "open_website", {"site": "youtube"}),
    ("close spotify", "close_app", {"name": "spotify"}),
    ("set a timer for 5 minutes", "set_timer", {"minutes": 5.0}),
    ("timer for thirty seconds", "set_timer", {"seconds": 30.0}),
    ("lock the laptop", "lock_screen", {}),
    ("shut down the laptop", "power", {"action": "shutdown"}),
    ("cancel shutdown", "cancel_shutdown", {}),
    ("what's the time", "get_datetime", {}),
    ("மணி என்ன", "get_datetime", {}),
    ("how much battery is left", "system_status", {"what": "battery"}),
])
def test_fast_router_matches(text, tool, args):
    route = FastRouter(AppCatalog()).match(text)
    assert route is not None, text
    assert route.tool == tool and route.args == args


@pytest.mark.parametrize("text", [
    "open the door", "can you explain how volume is calculated in geometry", "close your eyes",
    "what is a timer interrupt in embedded systems", "tell me a joke", "play",
])
def test_fast_router_leaves_conversation_to_the_model(text):
    route = FastRouter(AppCatalog()).match(text)
    assert route is None or (text == "play" and route.tool == "media_control")


def test_confirmation_words():
    assert confirmation_answer("Yes please") is True
    assert confirmation_answer("sari") is True
    assert confirmation_answer("சரி") is True
    assert confirmation_answer("no, cancel") is False
    assert confirmation_answer("வேண்டாம்") is False
    assert confirmation_answer("what's the weather") is None


# ----------------------------------------------------------------- validation
def test_argument_validation_and_urls():
    schema = {"type": "object", "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100},
                                               "action": {"type": "string", "enum": ["up", "down"]}},
              "required": ["action"]}
    assert validate_args(schema, {"action": "UP", "level": "40", "evil": "rm -rf"}) == {"action": "up", "level": 40}
    with pytest.raises(ToolArgumentError):
        validate_args(schema, {"action": "sideways"})
    with pytest.raises(ToolArgumentError):
        validate_args(schema, {"action": "up", "level": 400})
    with pytest.raises(ToolArgumentError):
        validate_args(schema, {})
    assert clean_url("youtube.com") == "https://youtube.com"
    assert clean_url("https://github.com/x") == "https://github.com/x"
    for bad in ("javascript:alert(1)", "file:///C:/Windows", "C:\\Windows\\system32\\cmd.exe", "a b.com", "localhost"):
        assert clean_url(bad) is None, bad


def test_time_parsing_and_status_sentence():
    from datetime import datetime
    now = datetime(2026, 10, 31, 18, 0)
    assert seconds_until("6:30 pm", now) == 1800
    assert seconds_until("17:00", now) == 23 * 3600
    assert seconds_until("25:00", now) is None
    s = status_sentence({"cpu_pct": 12, "battery_pct": 80, "charging": True})
    assert s == "CPU is at 12 percent, battery 80 percent and charging."


# --------------------------------------------------------------------- engine
def test_instant_command_skips_the_model(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(tmp_path)
        events, reply = await say(engine, convs, "volume up")
        assert provider.calls == []
        assert ("press", ("volume_up", 5)) in backend.actions
        assert reply == "Volume up."
        assert any(ev.kind == "tool" and ev.data["fast"] for ev in events)
    run(go())


def test_captions_say_where_the_conversation_started(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(tmp_path)
        seen = []
        agen = hub.subscribe(send_current_state=False)

        async def collect():
            async for ev in agen:
                seen.append(ev)

        task = asyncio.create_task(collect())
        await asyncio.sleep(0)
        session = convs.get_or_create(None)
        async for _ in engine.run(session, "sys", "volume up", source="AIRA Pi"):
            pass
        async for _ in engine.run(session, "sys", "mute"):
            pass
        await asyncio.sleep(0)
        task.cancel()
        caps = [(e["role"], e["text"], e["source"]) for e in seen if e["type"] == "caption"]
        assert caps == [("user", "volume up", "AIRA Pi"), ("aira", "Volume up.", "AIRA Pi"),
                        ("user", "mute", "web"), ("aira", caps[3][1], "web")]
        states = [e["state"] for e in seen if e["type"] == "state"]
        assert states.count("speaking") == 2              # once per answer, after its caption
    run(go())


def test_risky_action_waits_for_yes(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(tmp_path)
        events, reply = await say(engine, convs, "shut down the laptop")
        assert "Say yes to confirm" in reply and any(ev.kind == "confirm" for ev in events)
        assert backend.actions == []
        _, reply = await say(engine, convs, "yes")
        assert ("shutdown", None) in backend.actions
        assert "30 seconds" in reply
        # a "no" cancels
        await say(engine, convs, "restart the computer")
        _, reply = await say(engine, convs, "no")
        assert reply == "Okay, I won't."
        assert ("restart", None) not in backend.actions
    run(go())


def test_unrelated_reply_drops_pending_action(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(tmp_path, rounds=[("Sure, here's a joke.", [])])
        await say(engine, convs, "shutdown")
        await say(engine, convs, "tell me a joke")
        _, reply = await say(engine, convs, "yes")
        assert ("shutdown", None) not in backend.actions
    run(go())


def test_model_tool_call_action_is_spoken_without_second_round(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(
            tmp_path, rounds=[("", [("open_app", {"name": "spotify"})])])
        events, reply = await say(engine, convs, "I want to listen to some songs")
        assert ("launch", "spotify") in backend.actions
        assert reply == "Opening Spotify."
        assert len(provider.calls) == 1 and provider.calls[0]["tools"]
        assert any(ev.kind == "tool" and ev.data["name"] == "open_app" for ev in events)
    run(go())


def test_model_info_tool_goes_back_to_model(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(
            tmp_path, rounds=[("", [("get_datetime", {})]), ("It's evening, time to relax!", [])])
        _, reply = await say(engine, convs, "is it late?")
        assert reply == "It's evening, time to relax!"
        tool_msg = provider.calls[1]["messages"][-1]
        assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "get_datetime"
        assert json.loads(tool_msg["content"])["ok"] is True
    run(go())


def test_model_cannot_skip_confirmation(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(
            tmp_path, rounds=[("", [("power", {"action": "shutdown"})])])
        events, reply = await say(engine, convs, "I'm done for today, turn everything off")
        assert backend.actions == [] and "Say yes to confirm" in reply
        await say(engine, convs, "yes")
        assert ("shutdown", None) in backend.actions
    run(go())


def test_unknown_tool_and_bad_args_do_not_crash(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(
            tmp_path, rounds=[("", [("format_disk", {}), ("volume", {"action": "explode"})]),
                              ("Sorry, I can't do that.", [])])
        _, reply = await say(engine, convs, "do something weird")
        assert reply == "Sorry, I can't do that."
        assert backend.actions == []
    run(go())


def test_events_are_published_for_devices(tmp_path):
    async def go():
        engine, provider, backend, convs, hub = make_engine(tmp_path)
        seen = []
        agen = hub.subscribe(send_current_state=False)
        task = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0)
        await say(engine, convs, "mute")
        seen.append(await task)
        while True:
            try:
                seen.append(await asyncio.wait_for(agen.__anext__(), 0.05))
            except asyncio.TimeoutError:
                break
        kinds = [(e["type"], e.get("state") or e.get("role")) for e in seen]
        assert ("caption", "user") in kinds and ("state", "thinking") in kinds and ("caption", "aira") in kinds
        assert hub.state["state"] == "speaking" and hub.state["estimate_s"] > 0
    run(go())


# ------------------------------------------------------------------ reminders
def test_timer_fires_and_is_persisted(tmp_path):
    async def go():
        hub = EventHub()
        fired = []
        sched = Scheduler(hub, tmp_path / "rem.json", on_fire=fired.append)
        agen = hub.subscribe(send_current_state=False)
        nxt = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0)
        r = sched.add(1, "tea", kind="timer")
        assert json.loads((tmp_path / "rem.json").read_text())[0]["id"] == r.id
        ev = await asyncio.wait_for(nxt, 3)
        assert ev["type"] == "reminder" and "tea timer" in ev["say"]
        assert fired and json.loads((tmp_path / "rem.json").read_text()) == []
    run(go())


def test_missed_reminders_fire_on_start_and_cancel_works(tmp_path):
    async def go():
        store = tmp_path / "rem.json"
        store.write_text(json.dumps([{"id": "abc123", "due": time.time() - 60, "text": "call amma", "kind": "reminder"},
                                     {"id": "def456", "due": time.time() + 600, "text": "study", "kind": "reminder"}]))
        hub = EventHub()
        sched = Scheduler(hub, store)
        agen = hub.subscribe(send_current_state=False)
        nxt = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0)
        await sched.start()
        ev = await asyncio.wait_for(nxt, 1)
        assert ev["missed"] and "call amma" in ev["say"]
        assert [r.text for r in sched.list()] == ["study"]
        assert [r.text for r in sched.cancel("study")] == ["study"] and sched.list() == []
        await sched.stop()
    run(go())

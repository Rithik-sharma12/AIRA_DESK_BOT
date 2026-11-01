"""AIRA's built-in laptop skills.

Safe tools run immediately. ``close_app`` and ``power`` (sleep / shut down / restart)
need the user's spoken or tapped confirmation first; the agent engine enforces that.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import Any

from ..scheduler import Scheduler, describe_delay
from .apps import AppCatalog, search_url
from .base import Tool, ToolRegistry, ToolResult, t
from .system import SHUTDOWN_DELAY_S, VOLUME_STEP_PCT, Backend


# ----------------------------------------------------------------- status data
def _gpu() -> dict[str, Any] | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()[0]
        name, util, used, total, temp = [x.strip() for x in out.split(",")]
        return {"name": name, "util_pct": int(float(util)), "mem_used_mb": int(float(used)),
                "mem_total_mb": int(float(total)), "temp_c": int(float(temp))}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _internet() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 443), timeout=1.5):
            return True
    except OSError:
        return False


def collect_status(what: str = "all") -> dict[str, Any]:
    import psutil

    data: dict[str, Any] = {}
    if what in ("all", "cpu"):
        data["cpu_pct"] = round(psutil.cpu_percent(interval=0.3))
    if what in ("all", "memory"):
        vm = psutil.virtual_memory()
        data["ram_used_gb"] = round(vm.used / 1e9, 1)
        data["ram_total_gb"] = round(vm.total / 1e9, 1)
    if what in ("all", "battery"):
        b = psutil.sensors_battery()
        if b is not None:
            data["battery_pct"] = round(b.percent)
            data["charging"] = bool(b.power_plugged)
    if what in ("all", "gpu"):
        g = _gpu()
        if g:
            data["gpu"] = g
    if what in ("all", "disk"):
        root = "C:\\" if sys.platform == "win32" else "/"
        du = psutil.disk_usage(root)
        data["disk_free_gb"] = round(du.free / 1e9)
    if what in ("all", "network"):
        data["internet"] = _internet()
    return data


def status_sentence(d: dict[str, Any]) -> str:
    parts = []
    if "cpu_pct" in d:
        parts.append(f"CPU is at {d['cpu_pct']} percent")
    if "ram_used_gb" in d:
        parts.append(f"memory {d['ram_used_gb']} of {d['ram_total_gb']} gigabytes used")
    if "gpu" in d:
        g = d["gpu"]
        parts.append(f"GPU {g['util_pct']} percent busy at {g['temp_c']} degrees")
    if "battery_pct" in d:
        parts.append(f"battery {d['battery_pct']} percent" + (" and charging" if d.get("charging") else ""))
    if "disk_free_gb" in d:
        parts.append(f"{d['disk_free_gb']} gigabytes free on the main drive")
    if "internet" in d:
        parts.append("internet is connected" if d["internet"] else "there's no internet right now")
    if not parts:
        return "I couldn't read that on this laptop."
    text = ", ".join(parts)
    return text[0].upper() + text[1:] + "."


# ------------------------------------------------------------------- time
_AT_RE = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\s*$", re.I)


def seconds_until(at: str, now: datetime | None = None) -> float | None:
    m = _AT_RE.match(at)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    now = now or datetime.now()
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return (due - now).total_seconds()


# ------------------------------------------------------------------- registry
def build_registry(backend: Backend, catalog: AppCatalog, scheduler: Scheduler) -> ToolRegistry:
    app_list = ", ".join(catalog.app_names())

    # --- apps & web ----------------------------------------------------------
    async def open_app(a: dict, lang: str) -> ToolResult:
        app = catalog.find(a["name"])
        if not app:
            return ToolResult(False, t(lang, f"I can't open {a['name']} yet. I can open {app_list}.",
                                       f"{a['name']} திறக்க இன்னும் தெரியாது."), {"known_apps": catalog.app_names()})
        await asyncio.to_thread(backend.launch, app)
        return ToolResult(True, t(lang, f"Opening {app.name}.", f"{app.name} திறக்கிறேன்."))

    async def close_app(a: dict, lang: str) -> ToolResult:
        app = catalog.find(a["name"])
        if not app or not app.closable:
            return ToolResult(False, t(lang, f"I can't close {a['name']}.", f"{a['name']} மூட முடியாது."))
        n = await asyncio.to_thread(backend.close, app)
        if not n:
            return ToolResult(True, t(lang, f"{app.name} isn't open.", f"{app.name} திறந்திருக்கவில்லை."))
        return ToolResult(True, t(lang, f"Closing {app.name}.", f"{app.name} மூடுகிறேன்."))

    def _close_prompt(a: dict, lang: str) -> str:
        app = catalog.find(a.get("name", ""))
        name = app.name if app else a.get("name", "that app")
        return t(lang, f"Close {name}? Unsaved work may ask to be saved. Say yes to confirm.",
                 f"{name} மூடட்டுமா? சரி என்று சொல்லுங்கள்.")

    async def open_website(a: dict, lang: str) -> ToolResult:
        url = catalog.site_url(a["site"])
        if not url:
            return ToolResult(False, t(lang, "That doesn't look like a website address.", "அது website முகவரி போல இல்லை."))
        await asyncio.to_thread(backend.open_url, url)
        host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        return ToolResult(True, t(lang, f"Opening {host}.", f"{host} திறக்கிறேன்."))

    async def web_search(a: dict, lang: str) -> ToolResult:
        await asyncio.to_thread(backend.open_url, search_url(a["query"]))
        return ToolResult(True, t(lang, f"Searching the web for {a['query']}.", f"{a['query']} தேடுகிறேன்."))

    # --- status ----------------------------------------------------------------
    async def system_status(a: dict, lang: str) -> ToolResult:
        d = await asyncio.to_thread(collect_status, a.get("what", "all"))
        return ToolResult(True, status_sentence(d), d)

    async def get_datetime(a: dict, lang: str) -> ToolResult:
        now = datetime.now()
        return ToolResult(True, now.strftime("It's %I:%M %p on %A, %d %B.").replace(" 0", " "),
                          {"iso": now.isoformat(timespec="minutes")})

    # --- media / volume / brightness -------------------------------------------
    async def media_control(a: dict, lang: str) -> ToolResult:
        action = a["action"]
        await asyncio.to_thread(backend.press, action)
        words = {"play_pause": ("Okay.", "சரி."), "next": ("Next track.", "அடுத்த பாட்டு."),
                 "previous": ("Previous track.", "முந்தைய பாட்டு."), "stop": ("Stopped.", "நிறுத்திட்டேன்.")}
        en, ta = words[action]
        return ToolResult(True, t(lang, en, ta))

    async def volume(a: dict, lang: str) -> ToolResult:
        action = a["action"]
        if action == "mute":
            await asyncio.to_thread(backend.press, "mute")
            return ToolResult(True, t(lang, "Toggled mute.", "Mute மாற்றினேன்."))
        if action == "set":
            level = int(a.get("level", 50))
            await asyncio.to_thread(backend.press, "volume_down", 100 // VOLUME_STEP_PCT)
            await asyncio.to_thread(backend.press, "volume_up", round(level / VOLUME_STEP_PCT))
            return ToolResult(True, t(lang, f"Volume set to {level} percent.", f"Volume {level} சதவீதம்."))
        amount = int(a.get("amount", 10))
        presses = max(1, round(amount / VOLUME_STEP_PCT))
        await asyncio.to_thread(backend.press, "volume_up" if action == "up" else "volume_down", presses)
        return ToolResult(True, t(lang, f"Volume {'up' if action == 'up' else 'down'}.",
                                  "சத்தம் கூட்டினேன்." if action == "up" else "சத்தம் குறைத்தேன்."))

    async def brightness(a: dict, lang: str) -> ToolResult:
        action = a["action"]
        if action == "set":
            level = int(a.get("level", 70))
        else:
            current = await asyncio.to_thread(backend.get_brightness)
            if current is None:
                return ToolResult(False, t(lang, "I can't change the brightness of this screen.",
                                           "இந்த screen brightness மாற்ற முடியாது."))
            step = int(a.get("amount", 20))
            level = current + step if action == "up" else current - step
        level = max(5, min(100, level))  # never fully black
        ok = await asyncio.to_thread(backend.set_brightness, level)
        if not ok:
            return ToolResult(False, t(lang, "I can't change the brightness of this screen.",
                                       "இந்த screen brightness மாற்ற முடியாது."))
        return ToolResult(True, t(lang, f"Brightness {level} percent.", f"Brightness {level} சதவீதம்."))

    # --- timers & reminders ------------------------------------------------------
    async def set_timer(a: dict, lang: str) -> ToolResult:
        secs = float(a.get("minutes", 0)) * 60 + float(a.get("seconds", 0)) + float(a.get("hours", 0)) * 3600
        if secs < 1:
            return ToolResult(False, t(lang, "How long should the timer be?", "Timer எவ்வளவு நேரம்?"))
        r = scheduler.add(secs, a.get("label") or "timer", kind="timer")
        return ToolResult(True, t(lang, f"Timer set for {describe_delay(secs)}.",
                                  f"{describe_delay(secs)} timer வைத்தேன்."), {"id": r.id})

    async def set_reminder(a: dict, lang: str) -> ToolResult:
        if a.get("at"):
            secs = seconds_until(a["at"])
            if secs is None:
                return ToolResult(False, t(lang, "I didn't understand that time. Try like 7:30 pm.",
                                           "நேரம் புரியவில்லை. 7:30 pm மாதிரி சொல்லுங்கள்."))
        else:
            secs = float(a.get("minutes", 0)) * 60 + float(a.get("hours", 0)) * 3600
        if secs < 1:
            return ToolResult(False, t(lang, "When should I remind you?", "எப்போது நினைவூட்டணும்?"))
        r = scheduler.add(secs, a["text"], kind="reminder")
        when = datetime.fromtimestamp(r.due).strftime("%I:%M %p").lstrip("0")
        return ToolResult(True, t(lang, f"Okay, I'll remind you to {a['text']} at {when}.",
                                  f"சரி, {when}க்கு நினைவூட்டுகிறேன்."), {"id": r.id})

    async def list_reminders(a: dict, lang: str) -> ToolResult:
        items = scheduler.list()
        if not items:
            return ToolResult(True, t(lang, "You have no timers or reminders.", "Timer அல்லது reminder எதுவும் இல்லை."))
        now = time.time()
        desc = "; ".join(f"{r.kind} '{r.text}' in {describe_delay(r.remaining_s(now))}" for r in items[:5])
        return ToolResult(True, f"You have {len(items)}: {desc}.",
                          {"items": [{"id": r.id, "kind": r.kind, "text": r.text,
                                      "in_s": round(r.remaining_s(now))} for r in items]})

    async def cancel_reminder(a: dict, lang: str) -> ToolResult:
        gone = scheduler.cancel(a.get("which") or "all")
        if not gone:
            return ToolResult(False, t(lang, "I couldn't find that timer or reminder.", "அந்த reminder கிடைக்கவில்லை."))
        return ToolResult(True, t(lang, f"Cancelled {len(gone)}.", f"{len(gone)} ரத்து செய்தேன்."))

    # --- power -----------------------------------------------------------------
    async def lock_screen(a: dict, lang: str) -> ToolResult:
        await asyncio.to_thread(backend.lock)
        return ToolResult(True, t(lang, "Locking the laptop.", "Laptop lock செய்கிறேன்."))

    async def power(a: dict, lang: str) -> ToolResult:
        action = a["action"]
        if action == "sleep":
            await asyncio.to_thread(backend.sleep)
            return ToolResult(True, t(lang, "Good night! Going to sleep.", "தூங்கப் போகிறேன்."))
        await asyncio.to_thread(backend.shutdown, action == "restart")
        verb = "Restarting" if action == "restart" else "Shutting down"
        return ToolResult(True, t(lang, f"{verb} in {SHUTDOWN_DELAY_S} seconds. Say cancel shutdown to stop it.",
                                  f"{SHUTDOWN_DELAY_S} வினாடியில் {'restart' if action == 'restart' else 'shutdown'}. "
                                  "நிறுத்த cancel shutdown என்று சொல்லுங்கள்."))

    def _power_prompt(a: dict, lang: str) -> str:
        action = a.get("action", "shutdown")
        words = {"sleep": ("put the laptop to sleep", "laptop-ஐ sleep செய்யட்டுமா"),
                 "shutdown": ("shut down the laptop", "laptop-ஐ shutdown செய்யட்டுமா"),
                 "restart": ("restart the laptop", "laptop-ஐ restart செய்யட்டுமா")}
        en, ta = words.get(action, words["shutdown"])
        return t(lang, f"Do you want me to {en}? Say yes to confirm.", f"{ta}? சரி என்று சொல்லுங்கள்.")

    async def cancel_shutdown(a: dict, lang: str) -> ToolResult:
        ok = await asyncio.to_thread(backend.cancel_shutdown)
        return ToolResult(ok, t(lang, "Shutdown cancelled." if ok else "There was no shutdown to cancel.",
                                "Shutdown நிறுத்தினேன்." if ok else "நிறுத்த எதுவும் இல்லை."))

    obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
    return ToolRegistry([
        Tool("open_app", f"Open an app on the laptop. Known apps: {app_list}.",
             obj({"name": {"type": "string", "description": "App name, e.g. chrome, vs code, spotify",
                           "maxLength": 60}}, ["name"]), open_app),
        Tool("close_app", "Close an app that is open on the laptop.",
             obj({"name": {"type": "string", "maxLength": 60}}, ["name"]), close_app,
             risk="confirm", confirm_prompt=_close_prompt),
        Tool("open_website", "Open a website in the browser, e.g. youtube, gmail, github or a domain like example.com.",
             obj({"site": {"type": "string", "description": "Site name or domain/URL", "maxLength": 200}}, ["site"]),
             open_website),
        Tool("web_search", "Search the web in the browser.",
             obj({"query": {"type": "string", "maxLength": 200}}, ["query"]), web_search),
        Tool("system_status", "Read the laptop's live CPU, memory, GPU (usage and temperature), battery, disk "
             "and internet status. Use for any question about the laptop's state.",
             obj({"what": {"type": "string", "enum": ["all", "cpu", "memory", "gpu", "battery", "disk", "network"]}}),
             system_status, kind="info"),
        Tool("get_datetime", "Get the current date and time.", obj({}), get_datetime, kind="info"),
        Tool("media_control", "Control music/video playback (Spotify, YouTube, any player).",
             obj({"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"]),
             media_control),
        Tool("volume", "Change the laptop's sound volume.",
             obj({"action": {"type": "string", "enum": ["up", "down", "mute", "set"]},
                  "level": {"type": "integer", "minimum": 0, "maximum": 100, "description": "for set"},
                  "amount": {"type": "integer", "minimum": 2, "maximum": 50, "description": "percent for up/down"}},
                 ["action"]), volume),
        Tool("brightness", "Change the laptop screen brightness.",
             obj({"action": {"type": "string", "enum": ["up", "down", "set"]},
                  "level": {"type": "integer", "minimum": 5, "maximum": 100},
                  "amount": {"type": "integer", "minimum": 5, "maximum": 50}}, ["action"]), brightness),
        Tool("set_timer", "Start a countdown timer.",
             obj({"hours": {"type": "number", "minimum": 0, "maximum": 24},
                  "minutes": {"type": "number", "minimum": 0, "maximum": 1440},
                  "seconds": {"type": "number", "minimum": 0, "maximum": 86400},
                  "label": {"type": "string", "maxLength": 60}}), set_timer),
        Tool("set_reminder", "Remind the user about something later, either after some minutes/hours or at a clock "
             "time like '7:30 pm'.",
             obj({"text": {"type": "string", "description": "what to remind about", "maxLength": 200},
                  "minutes": {"type": "number", "minimum": 0, "maximum": 10080},
                  "hours": {"type": "number", "minimum": 0, "maximum": 168},
                  "at": {"type": "string", "description": "clock time, e.g. 18:30 or 6:30 pm", "maxLength": 12}},
                 ["text"]), set_reminder),
        Tool("list_reminders", "List active timers and reminders.", obj({}), list_reminders, kind="info"),
        Tool("cancel_reminder", "Cancel a timer or reminder (or all of them).",
             obj({"which": {"type": "string", "description": "words from it, 'timer', or 'all'", "maxLength": 60}}),
             cancel_reminder),
        Tool("lock_screen", "Lock the laptop screen.", obj({}), lock_screen),
        Tool("power", "Put the laptop to sleep, shut it down or restart it.",
             obj({"action": {"type": "string", "enum": ["sleep", "shutdown", "restart"]}}, ["action"]), power,
             risk="confirm", confirm_prompt=_power_prompt),
        Tool("cancel_shutdown", "Cancel a pending shutdown or restart.", obj({}), cancel_shutdown),
    ])

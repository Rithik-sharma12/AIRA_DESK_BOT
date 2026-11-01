"""Allowlist of apps and websites AIRA may open.

AIRA can only launch what is listed here (plus anything you add in ``config/apps.json``),
so the model can never run an arbitrary program. Names are matched loosely
("vs code", "vscode", "code" all find Visual Studio Code).

config/apps.json example::

    {
      "apps": {
        "obs": {"name": "OBS Studio", "aliases": ["obs"], "kind": "path",
                "target": "%ProgramFiles%\\\\obs-studio\\\\bin\\\\64bit\\\\obs64.exe",
                "process": "obs64.exe"}
      },
      "sites": {"sodak": "https://sodakedutech.in"}
    }
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus, urlsplit

log = logging.getLogger("aira.tools.apps")

AppKind = Literal["start", "cmd", "uri", "path"]


@dataclass
class AppEntry:
    key: str
    name: str
    target: str
    kind: AppKind = "start"
    process: str | None = None          # image name for closing, e.g. chrome.exe
    aliases: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    closable: bool = True


DEFAULT_APPS: list[AppEntry] = [
    AppEntry("chrome", "Google Chrome", "chrome", process="chrome.exe", aliases=["google chrome", "browser"]),
    AppEntry("edge", "Microsoft Edge", "msedge", process="msedge.exe", aliases=["microsoft edge"]),
    AppEntry("firefox", "Firefox", "firefox", process="firefox.exe", aliases=["mozilla"]),
    AppEntry("vscode", "VS Code", "code", kind="cmd", process="Code.exe",
             aliases=["vs code", "visual studio code", "code editor", "vscode"]),
    AppEntry("notepad", "Notepad", "notepad", process="Notepad.exe", aliases=["note pad"]),
    AppEntry("calculator", "Calculator", "calc", process="CalculatorApp.exe", aliases=["calc"]),
    AppEntry("explorer", "File Explorer", "explorer", aliases=["file explorer", "files", "my computer", "this pc"],
             closable=False),
    AppEntry("settings", "Settings", "ms-settings:", kind="uri", process="SystemSettings.exe",
             aliases=["windows settings", "system settings"]),
    AppEntry("taskmanager", "Task Manager", "taskmgr", process="Taskmgr.exe", aliases=["task manager"],
             closable=False),
    AppEntry("terminal", "Terminal", "wt", process="WindowsTerminal.exe",
             aliases=["windows terminal", "powershell", "command prompt", "cmd"]),
    AppEntry("spotify", "Spotify", "spotify:", kind="uri", process="Spotify.exe", aliases=["music"]),
    AppEntry("whatsapp", "WhatsApp", "whatsapp:", kind="uri", process="WhatsApp.exe", aliases=["whats app"]),
    AppEntry("word", "Microsoft Word", "winword", process="WINWORD.EXE", aliases=["ms word"]),
    AppEntry("excel", "Microsoft Excel", "excel", process="EXCEL.EXE", aliases=["ms excel"]),
    AppEntry("powerpoint", "PowerPoint", "powerpnt", process="POWERPNT.EXE", aliases=["ppt", "power point"]),
    AppEntry("paint", "Paint", "mspaint", process="mspaint.exe", aliases=["ms paint"]),
    AppEntry("camera", "Camera", "microsoft.windows.camera:", kind="uri", process="WindowsCamera.exe"),
    AppEntry("snipping", "Snipping Tool", "ms-screenclip:", kind="uri", aliases=["screenshot", "snipping tool"],
             closable=False),
]

DEFAULT_SITES: dict[str, str] = {
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "google": "https://www.google.com",
    "github": "https://github.com",
    "linkedin": "https://www.linkedin.com",
    "whatsapp web": "https://web.whatsapp.com",
    "stackoverflow": "https://stackoverflow.com",
    "leetcode": "https://leetcode.com",
    "codeforces": "https://codeforces.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "wikipedia": "https://www.wikipedia.org",
    "maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
}

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9-]{1,63}\.)+[a-z]{2,24}$")


def normalize(name: str) -> str:
    name = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    name = re.sub(r"\b(the|app|application|please|my)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


class AppCatalog:
    def __init__(self, apps: list[AppEntry] | None = None, sites: dict[str, str] | None = None):
        self.apps: dict[str, AppEntry] = {}
        for app in apps or DEFAULT_APPS:
            self.add(app)
        self.sites = dict(sites or DEFAULT_SITES)

    def add(self, app: AppEntry) -> None:
        self.apps[app.key] = app

    @classmethod
    def load(cls, path: Path | None) -> AppCatalog:
        cat = cls()
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for key, v in (data.get("apps") or {}).items():
                    cat.add(AppEntry(key=key, name=v.get("name", key), target=v["target"],
                                     kind=v.get("kind", "start"), process=v.get("process"),
                                     aliases=list(v.get("aliases", [])), args=list(v.get("args", [])),
                                     closable=bool(v.get("closable", True))))
                for name, url in (data.get("sites") or {}).items():
                    if clean_url(url):
                        cat.sites[normalize(name)] = url
                log.info("Loaded %d extra apps / %d sites from %s", len(data.get("apps") or {}),
                         len(data.get("sites") or {}), path)
            except (OSError, ValueError, KeyError) as exc:
                log.error("Ignoring %s: %s", path, exc)
        return cat

    def find(self, name: str) -> AppEntry | None:
        want = normalize(name)
        if not want:
            return None
        for app in self.apps.values():
            names = {normalize(app.key), normalize(app.name), *(normalize(a) for a in app.aliases)}
            if want in names or want.replace(" ", "") in {n.replace(" ", "") for n in names}:
                return app
        return None

    def app_names(self) -> list[str]:
        return [a.name for a in self.apps.values()]

    def site_url(self, name_or_url: str) -> str | None:
        key = normalize(name_or_url)
        if key in self.sites:
            return self.sites[key]
        return clean_url(name_or_url)


def clean_url(text: str) -> str | None:
    """Accept http(s) URLs or bare domains like 'youtube.com'; reject everything else."""
    text = text.strip()
    if not text or any(c.isspace() for c in text):
        return None
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not _HOST_RE.match(host):
        return None
    return text


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote_plus(query)

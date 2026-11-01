"""Operating-system layer for AIRA's tools.

``WindowsBackend`` does the real work on the HP Omen (Windows 11). ``DryRunBackend``
only records what it would have done; it is used by the tests and automatically on
any non-Windows machine, so developing or testing AIRA never touches the real system.

Nothing here builds a shell command from model text: apps come from an allowlist
(:mod:`.apps`), URLs are validated, and numbers are range-checked before they arrive.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

from .apps import AppEntry

log = logging.getLogger("aira.tools.system")

# Windows virtual-key codes (winuser.h)
VK = {
    "play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
}
VOLUME_STEP_PCT = 2          # one volume key press = 2 % on Windows
SHUTDOWN_DELAY_S = 30        # grace period; "cancel shutdown" aborts it


class Backend:
    name = "abstract"
    dry_run = False

    def launch(self, app: AppEntry) -> None: ...
    def close(self, app: AppEntry) -> int: ...          # number of processes asked to close
    def is_running(self, app: AppEntry) -> bool: ...
    def open_url(self, url: str) -> None: ...
    def press(self, key: str, times: int = 1) -> None: ...
    def get_brightness(self) -> int | None: ...
    def set_brightness(self, level: int) -> bool: ...
    def lock(self) -> None: ...
    def sleep(self) -> None: ...
    def shutdown(self, restart: bool = False) -> None: ...
    def cancel_shutdown(self) -> bool: ...
    def notify_sound(self) -> None: ...


@dataclass
class DryRunBackend(Backend):
    """Records actions instead of performing them."""

    name: str = "dry-run"
    dry_run: bool = True
    actions: list[tuple[str, Any]] = field(default_factory=list)
    brightness: int = 60
    running: set[str] = field(default_factory=set)

    def _rec(self, what: str, detail: Any = None) -> None:
        self.actions.append((what, detail))
        log.info("[dry-run] %s %s", what, detail if detail is not None else "")

    def launch(self, app: AppEntry) -> None:
        self._rec("launch", app.key)
        if app.process:
            self.running.add(app.process.lower())

    def close(self, app: AppEntry) -> int:
        self._rec("close", app.key)
        if app.process and app.process.lower() in self.running:
            self.running.discard(app.process.lower())
            return 1
        return 0

    def is_running(self, app: AppEntry) -> bool:
        return bool(app.process) and app.process.lower() in self.running

    def open_url(self, url: str) -> None:
        self._rec("open_url", url)

    def press(self, key: str, times: int = 1) -> None:
        self._rec("press", (key, times))

    def get_brightness(self) -> int | None:
        return self.brightness

    def set_brightness(self, level: int) -> bool:
        self._rec("brightness", level)
        self.brightness = level
        return True

    def lock(self) -> None:
        self._rec("lock")

    def sleep(self) -> None:
        self._rec("sleep")

    def shutdown(self, restart: bool = False) -> None:
        self._rec("restart" if restart else "shutdown")

    def cancel_shutdown(self) -> bool:
        self._rec("cancel_shutdown")
        return True

    def notify_sound(self) -> None:
        self._rec("beep")


class WindowsBackend(Backend):
    name = "windows"

    def __init__(self) -> None:
        import ctypes

        self._user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    # -------------------------------------------------------------- helpers
    def _run(self, args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              creationflags=self._no_window, check=False)

    def _spawn(self, args: list[str], cwd: str | None = None) -> None:
        subprocess.Popen(args, creationflags=self._no_window | getattr(subprocess, "DETACHED_PROCESS", 0),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, cwd=cwd)

    # --------------------------------------------------------------- apps
    def launch(self, app: AppEntry) -> None:
        if app.kind == "uri":
            os.startfile(app.target)  # type: ignore[attr-defined]  # ms-settings:, spotify: ...
        elif app.kind == "cmd":
            exe = shutil.which(app.target)
            if not exe:
                raise FileNotFoundError(f"{app.target} is not on PATH")
            self._spawn([exe, *app.args])
        elif app.kind == "path":
            exe = os.path.expandvars(app.target)
            self._spawn([exe, *app.args], cwd=os.path.dirname(exe) or None)  # some apps (OBS) need their own folder
        else:  # "start": lets Windows resolve App Paths (chrome, msedge, winword, ...)
            self._spawn(["cmd.exe", "/c", "start", "", app.target, *app.args])

    def close(self, app: AppEntry) -> int:
        if not app.process:
            return 0
        import psutil

        procs = [p for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == app.process.lower()]
        if procs:
            # Graceful close (no /F) so apps can ask to save unsaved work.
            self._run(["taskkill", "/IM", app.process])
        return len(procs)

    def is_running(self, app: AppEntry) -> bool:
        if not app.process:
            return False
        import psutil

        return any((p.info.get("name") or "").lower() == app.process.lower() for p in psutil.process_iter(["name"]))

    def open_url(self, url: str) -> None:
        os.startfile(url)  # type: ignore[attr-defined]  # default browser

    # -------------------------------------------------------------- keys
    def press(self, key: str, times: int = 1) -> None:
        vk = VK[key]
        for _ in range(max(1, min(times, 60))):
            self._user32.keybd_event(vk, 0, 0, 0)
            self._user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP

    # --------------------------------------------------------- brightness
    def get_brightness(self) -> int | None:
        r = self._run(["powershell", "-NoProfile", "-Command",
                       "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"])
        try:
            return int(r.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def set_brightness(self, level: int) -> bool:
        level = max(0, min(100, int(level)))
        r = self._run(["powershell", "-NoProfile", "-Command",
                       "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
                       f" | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={level}}}"])
        return r.returncode == 0

    # -------------------------------------------------------------- power
    def lock(self) -> None:
        self._user32.LockWorkStation()

    def sleep(self) -> None:
        # Note: if hibernation is enabled, Windows hibernates instead of sleeping here.
        self._spawn(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])

    def shutdown(self, restart: bool = False) -> None:
        self._run(["shutdown", "/r" if restart else "/s", "/t", str(SHUTDOWN_DELAY_S),
                   "/c", f"AIRA: {'restarting' if restart else 'shutting down'} in {SHUTDOWN_DELAY_S} seconds."])

    def cancel_shutdown(self) -> bool:
        return self._run(["shutdown", "/a"]).returncode == 0

    def notify_sound(self) -> None:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)


def create_backend(dry_run: bool | None) -> Backend:
    """``dry_run=None`` means: real actions on Windows, dry-run everywhere else."""
    if dry_run is None:
        dry_run = sys.platform != "win32"
    if dry_run:
        return DryRunBackend()
    return WindowsBackend()

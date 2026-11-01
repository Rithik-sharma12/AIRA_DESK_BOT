"""About AIRA: release details, the developer, and what this Pi is (read live from the system).

Developer details come from ``about.json`` next to this file, so they can be edited without
touching code. Everything about the hardware and OS is read from the running system; when a
value cannot be read (e.g. rendering screenshots on a PC) it says so instead of guessing.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import time
from pathlib import Path

from . import RELEASE_DATE, __version__

ABOUT_FILE = Path(__file__).with_name("about.json")
CPU_PARTS = {"0xd03": "Cortex-A53", "0xd08": "Cortex-A72", "0xd0b": "Cortex-A76", "0xb76": "ARM1176",
             "0xc07": "Cortex-A7"}


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip("\x00 \n")
    except OSError:
        return None


def developer() -> dict:
    try:
        return json.loads(ABOUT_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def board() -> str:
    return _read("/proc/device-tree/model") or f"{platform.machine() or '?'} computer"


def cpu() -> str:
    cores = os.cpu_count() or 0
    part = None
    for line in (_read("/proc/cpuinfo") or "").splitlines():
        if line.lower().startswith("cpu part"):
            part = CPU_PARTS.get(line.split(":")[-1].strip().lower())
            break
    khz = _read("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    speed = f" @ {int(khz) / 1e6:.1f} GHz" if khz and khz.isdigit() else ""
    return f"{cores} x {part or platform.processor() or platform.machine() or 'CPU'}{speed}"


def ram_mb() -> int | None:
    for line in (_read("/proc/meminfo") or "").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) // 1024
    return None


def storage() -> str:
    try:
        u = shutil.disk_usage("/")
        return f"{u.used / 1e9:.1f} of {u.total / 1e9:.0f} GB used"
    except OSError:
        return "unknown"


def os_name() -> str:
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return f"{platform.system()} {platform.release()}".strip()


def display() -> str:
    for fb in ("fb1", "fb0"):
        name = _read(f"/sys/class/graphics/{fb}/name")
        size = _read(f"/sys/class/graphics/{fb}/virtual_size")
        if name and "ili" in name.lower():
            return f'3.5" {name.replace("fb_", "").upper()} {size.replace(",", "x") if size else "480x320"}'
    return '3.5" ILI9486 480x320'


def uptime() -> str:
    raw = _read("/proc/uptime")
    if not raw:
        return "unknown"
    s = int(float(raw.split()[0]))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return (f"{d} d " if d else "") + f"{h} h {m} min"


def about_rows(edition: str = "AIRA Half", touch: str = "") -> list[tuple[str, str]]:
    """(label, value) rows for the About screen and `python3 -m aira_pi about`."""
    dev = developer()
    ram = ram_mb()
    rows = [("Version", f"{edition}  ·  aira_pi {__version__}  ·  released {release_text()}")]
    if dev.get("developer"):
        rows.append(("Developer", " · ".join(x for x in (dev.get("developer"), dev.get("organisation")) if x)))
    if dev.get("contact"):
        rows.append(("Contact", dev["contact"]))
    rows += [("Device", board()),
             ("CPU", cpu() + (f"  ·  RAM {ram} MB" if ram else "")),
             ("Storage", storage()),
             ("OS", os_name()),
             ("Kernel", f"{platform.release()} ({platform.machine()})  ·  Python {platform.python_version()}"),
             ("Screen", display() + (f"  ·  touch: {touch}" if touch else "")),
             ("Uptime", uptime())]
    return rows


def release_text() -> str:
    try:
        t = time.strptime(RELEASE_DATE, "%Y-%m-%d")
    except ValueError:
        return RELEASE_DATE
    return f"{t.tm_mday} {time.strftime('%b %Y', t)}"

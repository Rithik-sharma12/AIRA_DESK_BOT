"""Touch input from the 3.5" screen's resistive touch controller (ADS7846 / XPT2046).

The kernel exposes it as an input device (/dev/input/eventN). Raw readings run roughly
0..4095 on each axis and do not line up with the rotated 480x320 picture, so a one-time
calibration (``python3 -m aira_pi calibrate``) fits an affine map from four taps:

    x = a*rx + b*ry + c        y = d*rx + e*ry + f

Until calibrated, a best guess for the tft35a overlay with rotate=90 is used.
A *tap* is a touch released within 0.8 s that moved less than 25 px.
Needs python3-evdev (Debian package) and the user in the "input" group.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

log = logging.getLogger("aira_pi.touch")
NAMES = ("ads7846", "xpt2046", "touchscreen", "touch")


def find_touch_device(preferred: str | None = None):
    try:
        import evdev
    except ImportError:
        log.info("python3-evdev not installed: touch disabled")
        return None
    paths = [preferred] if preferred else evdev.list_devices()
    for path in paths:
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities().get(evdev.ecodes.EV_ABS, [])
        codes = {c[0] if isinstance(c, tuple) else c for c in caps}
        if evdev.ecodes.ABS_X in codes and evdev.ecodes.ABS_Y in codes and \
                (preferred or any(n in dev.name.lower() for n in NAMES)):
            return dev
        dev.close()
    return None


def default_calibration(xr: tuple[int, int], yr: tuple[int, int], size=(480, 320)) -> list[float]:
    """Guess for tft35a:rotate=90 (axes swapped, raw Y runs along the long side)."""
    w, h = size
    a, b, c = 0.0, w / (yr[1] - yr[0]), -yr[0] * w / (yr[1] - yr[0])
    d, e, f = h / (xr[1] - xr[0]), 0.0, -xr[0] * h / (xr[1] - xr[0])
    return [a, b, c, d, e, f]


def fit_calibration(raw: list[tuple[float, float]], screen: list[tuple[float, float]]) -> list[float]:
    """Least-squares affine map raw -> screen (needs 3+ points, 4 recommended)."""
    import numpy as np

    A = np.array([[rx, ry, 1.0] for rx, ry in raw])
    sx = np.array([p[0] for p in screen])
    sy = np.array([p[1] for p in screen])
    (a, b, c), *_ = np.linalg.lstsq(A, sx, rcond=None)
    (d, e, f), *_ = np.linalg.lstsq(A, sy, rcond=None)
    return [float(v) for v in (a, b, c, d, e, f)]


def apply_calibration(cal: list[float], rx: float, ry: float, size=(480, 320)) -> tuple[int, int]:
    a, b, c, d, e, f = cal
    x = a * rx + b * ry + c
    y = d * rx + e * ry + f
    return max(0, min(size[0] - 1, round(x))), max(0, min(size[1] - 1, round(y)))


class TapDetector:
    """Turns raw touch down / move / up into taps (screen coordinates)."""

    def __init__(self, to_screen: Callable[[float, float], tuple[int, int]], on_tap: Callable[[int, int], None],
                 max_s: float = 0.8, max_move: int = 25, clock=time.monotonic):
        self.to_screen = to_screen
        self.on_tap = on_tap
        self.max_s, self.max_move, self.clock = max_s, max_move, clock
        self.raw = [None, None]
        self.down_at: float | None = None
        self.start: tuple[int, int] | None = None
        self.last: tuple[int, int] | None = None

    def axis(self, which: int, value: int) -> None:
        self.raw[which] = value
        if self.down_at is not None and None not in self.raw:
            self.last = self.to_screen(*self.raw)
            if self.start is None:
                self.start = self.last

    def touch(self, down: bool) -> None:
        if down:
            self.down_at, self.start, self.last = self.clock(), None, None
            if None not in self.raw:
                self.start = self.last = self.to_screen(*self.raw)
            return
        if self.down_at is None:
            return
        held = self.clock() - self.down_at
        self.down_at = None
        if self.start is None or self.last is None:
            return
        moved = abs(self.last[0] - self.start[0]) + abs(self.last[1] - self.start[1])
        if held <= self.max_s and moved <= self.max_move:
            self.on_tap(*self.start)


class TouchScreen:
    def __init__(self, device, calibration: list[float] | None, on_tap: Callable[[int, int], None],
                 size=(480, 320)):
        import evdev

        self.dev = device
        self.ecodes = evdev.ecodes
        ax = device.absinfo(evdev.ecodes.ABS_X)
        ay = device.absinfo(evdev.ecodes.ABS_Y)
        self.x_range, self.y_range = (ax.min, ax.max), (ay.min, ay.max)
        self.calibration = calibration or default_calibration(self.x_range, self.y_range, size)
        self.calibrated = calibration is not None
        self.size = size
        self.detector = TapDetector(lambda rx, ry: apply_calibration(self.calibration, rx, ry, size), on_tap)
        self._thread: threading.Thread | None = None
        self._stop = False
        self.has_btn = False
        log.info("Touch: %s (%s)%s", device.name, device.path, "" if self.calibrated else
                 " - not calibrated yet: python3 -m aira_pi calibrate")

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="aira-touch", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        e = self.ecodes
        try:
            for ev in self.dev.read_loop():
                if self._stop:
                    return
                if ev.type == e.EV_ABS and ev.code in (e.ABS_X, e.ABS_Y):
                    self.detector.axis(0 if ev.code == e.ABS_X else 1, ev.value)
                elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
                    self.has_btn = True
                    self.detector.touch(bool(ev.value))
                elif ev.type == e.EV_ABS and ev.code == e.ABS_PRESSURE and not self.has_btn:
                    down = ev.value > 0                  # driver without BTN_TOUCH: pressure says up / down
                    if down != (self.detector.down_at is not None):
                        self.detector.touch(down)
        except OSError as exc:
            log.warning("touch device stopped: %s", exc)

    def close(self) -> None:
        self._stop = True
        try:
            self.dev.close()
        except OSError:
            pass


class PressReader:
    """Collects one press from raw evdev events, frame by frame (for calibration / touchtest).

    Works whether the driver marks presses with BTN_TOUCH, with ABS_PRESSURE, or with neither
    (then a pause of ``gap_s`` without events ends the press). Counts what it sees so a failed
    calibration can say whether the controller sent anything at all.
    """

    def __init__(self, ecodes, gap_s: float = 0.25):
        self.e = ecodes
        self.gap_s = gap_s
        self.cur: list[int | None] = [None, None]
        self.btn: bool | None = None          # None: driver never sent BTN_TOUCH
        self.pressure: int | None = None      # None: driver never sent ABS_PRESSURE
        self.xs: list[int] = []
        self.ys: list[int] = []
        self.last_sample = 0.0
        self.counts = {"events": 0, "xy": 0, "btn": 0, "pressure": 0}

    def touching(self) -> bool:
        if self.btn is not None:
            return self.btn
        if self.pressure is not None:
            return self.pressure > 0
        return True

    def _result(self):
        if not self.xs:
            return None
        xs, ys = sorted(self.xs), sorted(self.ys)    # median: ignores noisy first / last samples
        self.xs, self.ys = [], []
        return float(xs[len(xs) // 2]), float(ys[len(ys) // 2])

    def feed(self, ev, now: float):
        """One event in; returns (raw_x, raw_y) when a press has ended, else None."""
        e = self.e
        self.counts["events"] += 1
        if ev.type == e.EV_ABS and ev.code in (e.ABS_X, e.ABS_Y):
            self.counts["xy"] += 1
            self.cur[0 if ev.code == e.ABS_X else 1] = ev.value
        elif ev.type == e.EV_ABS and ev.code == e.ABS_PRESSURE:
            self.counts["pressure"] += 1
            was = self.pressure is not None and self.pressure > 0
            self.pressure = ev.value
            if self.btn is None and was and ev.value == 0:
                return self._result()
        elif ev.type == e.EV_KEY and ev.code == e.BTN_TOUCH:
            self.counts["btn"] += 1
            self.btn = bool(ev.value)
            if not ev.value:
                return self._result()
            self.xs, self.ys = [], []
        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
            if self.touching() and None not in self.cur:
                self.xs.append(self.cur[0])
                self.ys.append(self.cur[1])
                self.last_sample = now
        return None

    def idle(self, now: float):
        """Called when no events arrived for a while: ends a press if the driver never says 'up'."""
        if self.xs and now - self.last_sample >= self.gap_s and (self.btn is None or not self.btn):
            return self._result()
        return None

    def summary(self) -> str:
        c = self.counts
        if not c["events"]:
            return "the touch controller sent nothing at all"
        return (f"{c['events']} events: {c['xy']} position, {c['btn']} touch up/down, "
                f"{c['pressure']} pressure, but no complete press")


def read_raw_tap(device, timeout: float = 30.0, reader: PressReader | None = None) -> tuple[float, float] | None:
    """For calibration: the median raw position of one press (blocking). Pass ``reader`` to keep its counts."""
    import select

    import evdev

    reader = reader or PressReader(evdev.ecodes)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r, _, _ = select.select([device.fd], [], [], 0.1)
        now = time.monotonic()
        if not r:
            got = reader.idle(now)
            if got:
                return got
            continue
        for ev in device.read():
            got = reader.feed(ev, now)
            if got:
                return got
    return None


def input_report() -> list[str]:
    """Plain-language list of input devices, for touchtest / failed calibration."""
    import glob
    import os

    lines = []
    paths = sorted(glob.glob("/dev/input/event*"))
    if not paths:
        return ["no /dev/input/event* devices: the touch driver (ads7846) is not loaded"]
    try:
        import evdev
    except ImportError:
        return ["python3-evdev is not installed (sudo bash install.sh)"]
    for path in paths:
        try:
            dev = evdev.InputDevice(path)
            lines.append(f"{path}: {dev.name}")
            dev.close()
        except PermissionError:
            lines.append(f"{path}: no permission (user not in the 'input' group yet: "
                         "run install.sh, then log out and back in)")
        except OSError as exc:
            lines.append(f"{path}: {exc}")
    if "input" not in {__import__("grp").getgrgid(g).gr_name for g in os.getgroups()}:
        lines.append("this login is not in the 'input' group yet (log out and back in after install.sh)")
    return lines


def touch_irq_count() -> int | None:
    """Interrupts the touch controller has raised (/proc/interrupts), or None if not listed."""
    try:
        with open("/proc/interrupts", encoding="ascii", errors="ignore") as fh:
            for line in fh:
                if any(n in line.lower() for n in ("ads7846", "xpt2046")):
                    parts = line.split()
                    return sum(int(p) for p in parts[1:] if p.isdigit())
    except OSError:
        pass
    return None

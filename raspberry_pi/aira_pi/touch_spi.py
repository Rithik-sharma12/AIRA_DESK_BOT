"""Touch without the pen-interrupt line: read the XPT2046 / ADS7846 chip directly over SPI.

The kernel's ads7846 driver only reads the chip after its PENIRQ line (GPIO17) falls. On
AIRA's screen that line stays low all the time, so the kernel never reads a touch. This
module asks the chip for position and pressure about 60 times a second instead, through
/dev/spidev0.1, so the interrupt line does not matter.

``touch_spi.sh enable`` (as root) gives spi0.1 to the generic spidev driver at every boot;
``touch_spi.sh disable`` gives it back to the kernel's ads7846 driver. Pure Python (ioctl),
no extra packages.

Chip commands (12-bit, differential, power-down between conversions):
    0xD0 X   0x90 Y   0xB0 Z1   0xC0 Z2      pressure = Z1 + 4095 - Z2 (grows with a press)
"""

from __future__ import annotations

import ctypes
import fcntl
import logging
import os
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .touch import TapDetector, apply_calibration, default_calibration

log = logging.getLogger("aira_pi.touch_spi")

SPIDEV = "/dev/spidev0.1"
SPI_DEVICE = Path("/sys/bus/spi/devices/spi0.1")
SPI_IOC_WR_MODE = 0x40016B01
SPI_IOC_WR_MAX_SPEED_HZ = 0x40046B04
SPI_IOC_MESSAGE_1 = 0x40206B00          # _IOW('k', 0, struct spi_ioc_transfer[1]), 32 bytes
CMD_X, CMD_Y, CMD_Z1, CMD_Z2 = 0xD0, 0x90, 0xB0, 0xC0
RAW_RANGE = (0, 4095)
DEFAULT_THRESHOLD = 400                 # pressure above this = finger down (tunable: "touch_spi_threshold")


def spi_bound_driver() -> str | None:
    """Which kernel driver owns the touch chip's SPI address: 'ads7846', 'spidev' or None."""
    try:
        return os.path.basename(os.readlink(SPI_DEVICE / "driver"))
    except OSError:
        return None


def spi_touch_available(path: str = SPIDEV) -> bool:
    return os.path.exists(path) and spi_bound_driver() == "spidev"


class SpiDev:
    """Minimal /dev/spidevB.C full-duplex transfers (mode 0) through ioctl."""

    def __init__(self, path: str = SPIDEV, speed_hz: int = 1_000_000):
        self.fd = os.open(path, os.O_RDWR)
        self.speed_hz = speed_hz
        fcntl.ioctl(self.fd, SPI_IOC_WR_MODE, struct.pack("B", 0))
        fcntl.ioctl(self.fd, SPI_IOC_WR_MAX_SPEED_HZ, struct.pack("<I", speed_hz))

    def xfer(self, data: bytes) -> bytes:
        tx = ctypes.create_string_buffer(bytes(data), len(data))
        rx = ctypes.create_string_buffer(len(data))
        msg = struct.pack("<QQIIHBBBBBB", ctypes.addressof(tx), ctypes.addressof(rx), len(data),
                          self.speed_hz, 0, 8, 0, 0, 0, 0, 0)
        fcntl.ioctl(self.fd, SPI_IOC_MESSAGE_1, ctypes.create_string_buffer(msg, len(msg)))
        return rx.raw

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class XPT2046:
    def __init__(self, spi):
        self.spi = spi

    def read(self, cmd: int) -> int:
        r = self.spi.xfer(bytes((cmd, 0, 0)))
        return ((r[1] << 8) | r[2]) >> 3

    def sample(self) -> tuple[int, int, int, int, int]:
        """(x, y, pressure, z1, z2); x and y are the median of three readings."""
        z1, z2 = self.read(CMD_Z1), self.read(CMD_Z2)
        xs = sorted(self.read(CMD_X) for _ in range(3))
        ys = sorted(self.read(CMD_Y) for _ in range(3))
        pressure = z1 + 4095 - z2 if z1 else 0
        return xs[1], ys[1], pressure, z1, z2


class SpiPress:
    """Turns pressure samples into presses (for calibration and touchtest)."""

    def __init__(self, threshold: int = DEFAULT_THRESHOLD, release_samples: int = 2):
        self.threshold, self.release_samples = threshold, release_samples
        self.xs: list[int] = []
        self.ys: list[int] = []
        self.quiet = 0

    def feed(self, x: int, y: int, pressure: int) -> tuple[float, float] | None:
        if pressure > self.threshold and 0 < x < 4095 and 0 < y < 4095:
            self.xs.append(x)
            self.ys.append(y)
            self.quiet = 0
            return None
        if self.xs:
            self.quiet += 1
            if self.quiet >= self.release_samples:          # a short gap ends the press
                xs, ys = sorted(self.xs), sorted(self.ys)
                self.xs, self.ys, self.quiet = [], [], 0
                return float(xs[len(xs) // 2]), float(ys[len(ys) // 2])
        return None


class SpiTouchScreen:
    """Same interface as touch.TouchScreen, but polling the chip instead of waiting for IRQs."""

    def __init__(self, chip: XPT2046, calibration: list[float] | None, on_tap: Callable[[int, int], None],
                 size=(480, 320), threshold: int = DEFAULT_THRESHOLD, hz: float = 60.0):
        self.chip = chip
        self.calibration = calibration or default_calibration(RAW_RANGE, RAW_RANGE, size)
        self.calibrated = calibration is not None
        self.threshold, self.period = threshold, 1.0 / hz
        self.detector = TapDetector(lambda rx, ry: apply_calibration(self.calibration, rx, ry, size), on_tap)
        self._stop = False
        self._thread: threading.Thread | None = None
        log.info("Touch: XPT2046 over SPI (%s), polling %.0f Hz%s", SPIDEV, hz,
                 "" if self.calibrated else " - not calibrated yet: python3 -m aira_pi calibrate")

    def poll_once(self) -> None:
        x, y, pressure, _, _ = self.chip.sample()
        down = pressure > self.threshold and 0 < x < 4095 and 0 < y < 4095
        if down:
            self.detector.axis(0, x)
            self.detector.axis(1, y)
        if down != (self.detector.down_at is not None):
            self.detector.touch(down)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="aira-touch-spi", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        errors = 0
        while not self._stop:
            try:
                self.poll_once()
                errors = 0
            except OSError as exc:
                errors += 1
                if errors == 5:
                    log.warning("SPI touch read failing: %s", exc)
                time.sleep(0.5)
            time.sleep(self.period)

    def close(self) -> None:
        self._stop = True
        close = getattr(self.chip.spi, "close", None)
        if close:
            close()


def open_spi_touch(path: str = SPIDEV) -> XPT2046 | None:
    if not spi_touch_available(path):
        return None
    try:
        return XPT2046(SpiDev(path))
    except OSError as exc:
        log.warning("Cannot open %s: %s (user in the 'spi' group? run touch_spi.sh enable)", path, exc)
        return None


def read_raw_tap_spi(chip: XPT2046, timeout: float = 30.0, threshold: int = DEFAULT_THRESHOLD,
                     samples: list | None = None) -> tuple[float, float] | None:
    """For calibration / touchtest: the median raw position of one press (blocking).
    Pass a list as ``samples`` to collect every (x, y, pressure, z1, z2) reading."""
    press = SpiPress(threshold)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        x, y, p, z1, z2 = chip.sample()
        if samples is not None:
            samples.append((x, y, p, z1, z2))
        got = press.feed(x, y, p)
        if got:
            return got
        time.sleep(1 / 60)
    return None


def diagnose(samples: list, threshold: int = DEFAULT_THRESHOLD) -> str:
    """Plain-language verdict from the readings of a touchtest."""
    if not samples:
        return "no readings"
    # A silent chip returns the same value (all 0s or all 1s) for every command; an idle one
    # still differs between commands (Z1 = 0 but Z2 = 4095), so that is not mistaken for silence.
    if all(x == y == z1 == z2 for x, y, _, z1, z2 in samples):
        return "the touch chip does not answer on SPI (every reading identical): wiring or chip problem"
    pressed = sum(p > threshold for _, _, p, _, _ in samples) / len(samples)
    if pressed > 0.95:
        return ("the chip reports a press ALL the time: something presses the touch layer "
                "(case, bezel, tape, cable) or the layer is damaged")
    if pressed == 0:
        return "the chip answers but never saw a press: press harder (fingernail or stylus)"
    return "touch works over SPI: presses were seen and released"

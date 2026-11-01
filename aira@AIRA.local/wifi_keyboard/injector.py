"""Creates a virtual keyboard with Linux uinput and injects key events into it.

The kernel treats it exactly like a USB keyboard: it types into the active text console
on the TFT (no desktop needed) or into any app that reads input devices.
Requires /dev/uinput access (the installer sets this up) and python3-evdev.
"""

from __future__ import annotations

import time

from .keymap import ALLOWED, KEY, MODIFIERS, Stroke


class KeyInjector:
    """Interface + bookkeeping. Subclasses implement _emit(code, value)."""

    def __init__(self, key_delay_s: float = 0.004):
        self.pressed: set[str] = set()
        self.key_delay_s = key_delay_s

    def _emit(self, code: int, value: int) -> None:  # value: 1 down, 0 up
        raise NotImplementedError

    def _check(self, name: str) -> int:
        if name not in ALLOWED:
            raise ValueError(f"key not allowed: {name}")
        return KEY[name]

    def down(self, name: str) -> None:
        code = self._check(name)
        self._emit(code, 1)
        self.pressed.add(name)

    def up(self, name: str) -> None:
        code = self._check(name)
        self._emit(code, 0)
        self.pressed.discard(name)

    def tap(self, name: str, mods: list[str] | tuple[str, ...] = ()) -> None:
        for m in mods:
            if m not in MODIFIERS:
                raise ValueError(f"not a modifier: {m}")
        self._check(name)
        for m in mods:
            self.down(m)
        self.down(name)
        self._pause()
        self.up(name)
        for m in reversed(list(mods)):
            self.up(m)
        self._pause()

    def type_strokes(self, strokes: list[Stroke], mods: list[str] | tuple[str, ...] = ()) -> None:
        for s in strokes:
            extra = ("KEY_LEFTSHIFT",) if s.shift and "KEY_LEFTSHIFT" not in mods else ()
            self.tap(s.key, tuple(mods) + extra)

    def release_all(self) -> None:
        """Called on disconnect so no modifier stays stuck down."""
        for name in sorted(self.pressed):
            self._emit(KEY[name], 0)
        self.pressed.clear()

    def _pause(self) -> None:
        if self.key_delay_s:
            time.sleep(self.key_delay_s)

    def close(self) -> None:
        self.release_all()


class UInputInjector(KeyInjector):
    NAME = "AIRA Wi-Fi Keyboard"

    def __init__(self, key_delay_s: float = 0.004):
        super().__init__(key_delay_s)
        try:
            from evdev import UInput, ecodes
        except ImportError as exc:
            raise RuntimeError("python3-evdev missing: sudo apt install python3-evdev") from exc
        self._ecodes = ecodes
        try:
            self._ui = UInput({ecodes.EV_KEY: sorted(KEY.values())}, name=self.NAME)
        except OSError as exc:
            raise RuntimeError(
                f"cannot open /dev/uinput ({exc}). Run install.sh, or check 'lsmod | grep uinput'."
            ) from exc

    def _emit(self, code: int, value: int) -> None:
        self._ui.write(self._ecodes.EV_KEY, code, value)
        self._ui.syn()

    def close(self) -> None:
        super().close()
        self._ui.close()


class RecordingInjector(KeyInjector):
    """For tests and --dry-run: records events instead of touching the kernel."""

    def __init__(self):
        super().__init__(key_delay_s=0)
        self.events: list[tuple[int, int]] = []

    def _emit(self, code: int, value: int) -> None:
        self.events.append((code, value))

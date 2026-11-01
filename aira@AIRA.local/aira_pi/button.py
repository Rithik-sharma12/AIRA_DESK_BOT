"""AIRA's push button on GPIO26 (physical pin 37) to GND (pin 39), internal pull-up.

- short press        : talk to AIRA (Pi audio: hold to talk; otherwise wakes the web page's mic)
- hold 1.5 s         : show the status screen (IP address, laptop link, Wi-Fi keyboard URL)
Uses gpiozero (python3-gpiozero + python3-lgpio on Debian 13). Without them the app
runs fine, just without the button.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

log = logging.getLogger("aira_pi.button")


class Button:
    def __init__(self, gpio: int, loop: asyncio.AbstractEventLoop, on_event: Callable[[str], None],
                 hold_s: float = 1.5):
        self.available = False
        self._held = False
        try:
            from gpiozero import Button as GZButton
        except ImportError:
            log.warning("gpiozero not installed: button disabled (sudo apt install python3-gpiozero python3-lgpio)")
            return
        try:
            self._btn = GZButton(gpio, pull_up=True, bounce_time=0.05, hold_time=hold_s)
        except Exception as exc:  # no GPIO access, pin busy, not a Pi
            log.warning("button on GPIO%d unavailable: %s", gpio, exc)
            return
        emit = lambda name: loop.call_soon_threadsafe(on_event, name)  # noqa: E731  gpiozero uses its own thread

        def pressed() -> None:
            self._held = False
            emit("down")

        def held() -> None:
            self._held = True
            emit("hold")

        def released() -> None:
            emit("up" if not self._held else "hold_released")

        self._btn.when_pressed = pressed
        self._btn.when_held = held
        self._btn.when_released = released
        self.available = True
        log.info("Button ready on GPIO%d", gpio)

    def close(self) -> None:
        if self.available:
            self._btn.close()

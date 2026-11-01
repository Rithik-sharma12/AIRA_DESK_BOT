"""Pi settings, stored in ~/.config/aira-pi/config.json (readable only by you).

Written by ``python3 -m aira_pi pair``. Holds the laptop's address (optional: the Pi
finds it automatically), the device token from pairing and, for HTTPS, the laptop's
certificate so the Pi only ever talks to *your* laptop.
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_path() -> Path:
    if os.environ.get("AIRA_PI_CONFIG"):
        return Path(os.environ["AIRA_PI_CONFIG"])
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "aira-pi" / "config.json"


@dataclass
class PiConfig:
    device_id: str = field(default_factory=lambda: "aira-pi-" + socket.gethostname().lower()[:20])
    name: str = "AIRA Pi"
    brain: str = "laptop"                # laptop | online | auto (laptop when connected, else online)
    online_service: str = ""             # openai, gemini, groq, openrouter, deepseek, ... (online.SERVICES)
    online_base_url: str = ""            # or any OpenAI-compatible URL
    online_model: str = ""
    online_api_key: str | None = None    # secret: never printed (python3 -m aira_pi online setup)
    chat_page: bool = True               # chat page for your phone on http://<pi>:<chat_port>/
    chat_port: int = 8770
    chat_code: str | None = None         # 6 digits, made on first start, shown on the System screen
    controller: bool = True              # ESP32 joystick / wireless controllers (aira_pi/controller.py)
    controller_port: int = 8771
    controller_token: str | None = None  # made on first start; python3 -m aira_pi controller shows it
    edition: str = "half"                # half: screen only (no mic, speaker, battery) | full: the complete handset
    laptop_url: str | None = None        # e.g. https://192.168.43.20:8765 ; None = discover
    token: str | None = None
    cert_pem: str | None = None          # pinned laptop certificate (HTTPS)
    fingerprint: str | None = None       # sha256 of that certificate
    discovery_port: int = 8766
    button_gpio: int = 26
    framebuffer: str | None = None       # default: auto-detect the ILI9486 (/dev/fb1)
    rotate: int = 0                      # extra rotation (0/180) if the face is upside down
    theme: str = "auto"                  # auto/brutal (design system + touch menu) | mascot (night) | simple
    touch: str = "auto"                  # auto (SPI reader if touch_spi.sh enabled it, else kernel) | evdev | spi | off
    touch_spi_threshold: int = 400       # SPI reader: pressure above this counts as a press
    touch_device: str | None = None      # e.g. /dev/input/event0 (default: find the ADS7846)
    touch_cal: list[float] | None = None # written by "python3 -m aira_pi calibrate"
    fps: int = 8
    audio: str = "auto"                  # auto | off | <ALSA card name>

    @property
    def paired(self) -> bool:
        return bool(self.token)

    @classmethod
    def load(cls, path: Path | None = None) -> PiConfig:
        path = path or config_path()
        cfg = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
        if os.environ.get("AIRA_PI_LAPTOP"):
            cfg.laptop_url = os.environ["AIRA_PI_LAPTOP"].rstrip("/")
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = path or config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)
        tmp.replace(path)
        return path

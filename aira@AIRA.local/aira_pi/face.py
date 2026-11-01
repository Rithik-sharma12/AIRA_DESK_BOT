"""AIRA's animated face for the 480x320 screen.

Layout (landscape):
    y   0- 26  status bar: link dot + "AIRA" on the left, the time on the right
    y  26-246  the face: two rounded eyes and a mouth
    y 246-320  captions: what AIRA (or you) just said, up to two lines

The colours match the web interface: cyan eyes, green while listening, violet while
thinking, red on errors. Expressions are the ten from the shared protocol.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 480, 320
BG = (11, 15, 23)
INK = (231, 237, 247)
MUTED = (138, 150, 171)
CYAN = (56, 189, 248)
GREEN = (52, 211, 153)
VIOLET = (167, 139, 250)
RED = (248, 113, 113)
AMBER = (251, 191, 36)

EYE_W, EYE_H, EYE_R = 74, 104, 30
EYE_Y = 120
EYE_X = (160, 320)
MOUTH_Y = 206
CAPTION_TOP = 250

FONT_DIRS = ("/usr/share/fonts/truetype/noto", "/usr/share/fonts/opentype/noto", "/usr/share/fonts/opentype/unifont",
             "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/opentype", "/usr/share/fonts/truetype")
TAMIL_FONTS = ("NotoSansTamil-Regular.ttf", "NotoSansTamilUI-Regular.ttf", "Lohit-Tamil.ttf")
LATIN_FONTS = ("NotoSans-Regular.ttf", "DejaVuSans.ttf")
BOLD_FONTS = ("NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf")


def _find_font(names: tuple[str, ...]) -> str | None:
    for d in FONT_DIRS:
        for n in names:
            p = Path(d) / n
            if p.exists():
                return str(p)
        base = Path(d)
        if base.exists():
            for n in names:
                hits = list(base.rglob(n))
                if hits:
                    return str(hits[0])
    return None


def _font(names: tuple[str, ...], size: int):
    path = _find_font(names)
    if path:
        try:
            return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
        except (OSError, ImportError, KeyError):
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


_TAMIL = range(0x0B80, 0x0C00)


def has_tamil(text: str) -> bool:
    return any(ord(c) in _TAMIL for c in text)


@dataclass
class FaceState:
    expression: str = "sleep"
    caption: str = ""
    caption_role: str = "aira"            # aira | user | system
    caption_until: float = 0.0
    hold_until: float = 0.0               # return to idle after this (from DISPLAY_STATE_CHANGED hold_s)
    linked: bool = False
    status_text: str = ""
    info_until: float = 0.0
    info_lines: list[str] = field(default_factory=list)
    flash_until: float = 0.0              # reminder highlight


class FaceRenderer:
    def __init__(self, size: tuple[int, int] = (W, H), seed: int | None = None):
        self.size = size
        self.state = FaceState()
        self._rng = random.Random(seed)
        self._next_blink = time.monotonic() + 3
        self._blink_start = -10.0
        self.f_caption_lat = _font(LATIN_FONTS, 21)
        self.f_caption_ta = _font(TAMIL_FONTS, 20) if _find_font(TAMIL_FONTS) else self.f_caption_lat
        self.f_small = _font(LATIN_FONTS, 15)
        self.f_bold = _font(BOLD_FONTS, 16)
        self.f_big = _font(BOLD_FONTS, 30)

    # --------------------------------------------------------------- state
    def set_expression(self, expression: str, hold_s: float | None = None, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.state.expression = expression
        self.state.hold_until = now + hold_s if hold_s else 0.0

    def set_caption(self, text: str, role: str = "aira", seconds: float = 12.0, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.state.caption = " ".join(text.split())[:300]
        self.state.caption_role = role
        self.state.caption_until = now + seconds

    def show_info(self, lines: list[str], seconds: float = 10.0) -> None:
        self.state.info_lines = lines
        self.state.info_until = time.monotonic() + seconds

    def flash(self, seconds: float = 6.0) -> None:
        self.state.flash_until = time.monotonic() + seconds

    def notify(self, text: str, seconds: float = 4.0) -> None:
        self.set_caption(text, "system", seconds)

    def animating(self, now: float | None = None) -> bool:
        """True when frames change on their own (so the app should keep redrawing quickly)."""
        now = time.monotonic() if now is None else now
        return self.state.expression in ("listening", "thinking", "speaking", "sleep", "excited") \
            or now < self.state.flash_until or now - self._blink_start < 0.3

    # --------------------------------------------------------------- render
    def render(self, now: float | None = None) -> Image.Image:
        now = time.monotonic() if now is None else now
        s = self.state
        if s.hold_until and now >= s.hold_until:
            s.expression, s.hold_until = "idle", 0.0
        img = Image.new("RGB", self.size, BG)
        d = ImageDraw.Draw(img)
        if now < s.info_until:
            self._info(d)
            return img
        expr = s.expression if s.linked or s.expression in ("error",) else "sleep"
        if now < s.flash_until and expr in ("idle", "speaking"):
            expr = "excited"
        self._status_bar(d, now)
        self._face(d, expr, now)
        self._caption(d, now)
        return img

    def _status_bar(self, d: ImageDraw.ImageDraw, now: float) -> None:
        s = self.state
        d.ellipse((10, 9, 20, 19), fill=GREEN if s.linked else AMBER)
        d.text((28, 4), "AIRA", font=self.f_bold, fill=INK)
        if s.status_text:
            d.text((86, 6), s.status_text[:36], font=self.f_small, fill=MUTED)
        clock = time.strftime("%H:%M")
        d.text((self.size[0] - 10, 4), clock, font=self.f_bold, fill=MUTED, anchor="ra")

    def _blink_amount(self, now: float) -> float:
        if now >= self._next_blink:
            self._blink_start = now
            self._next_blink = now + self._rng.uniform(2.5, 6.0)
        t = now - self._blink_start
        return 1.0 - abs(t / 0.12 - 1.0) if t < 0.24 else 0.0   # 0 open .. 1 closed

    def _eyes(self, d, color, scale_y=1.0, dx=0, dy=0, closed=0.0, sizes=(1.0, 1.0)) -> None:
        for i, cx in enumerate(EYE_X):
            k = sizes[i]
            h = max(6, EYE_H * scale_y * k * (1 - 0.92 * closed))
            w = EYE_W * k
            box = (cx - w / 2 + dx, EYE_Y - h / 2 + dy, cx + w / 2 + dx, EYE_Y + h / 2 + dy)
            d.rounded_rectangle(box, radius=min(EYE_R, h / 2), fill=color)

    def _face(self, d: ImageDraw.ImageDraw, expr: str, now: float) -> None:
        cx = self.size[0] // 2
        if expr in ("idle", "blinking"):
            self._eyes(d, CYAN, closed=1.0 if expr == "blinking" else self._blink_amount(now))
            d.arc((cx - 34, MOUTH_Y - 26, cx + 34, MOUTH_Y + 10), 20, 160, fill=CYAN, width=6)
        elif expr == "listening":
            pulse = 0.5 + 0.5 * math.sin(now * 5)
            self._eyes(d, GREEN, scale_y=1.08)
            for i, x in enumerate(range(cx - 36, cx + 37, 18)):   # sound bars
                h = 8 + 22 * abs(math.sin(now * 7 + i * 1.3)) * (0.6 + 0.4 * pulse)
                d.rounded_rectangle((x - 5, MOUTH_Y - h / 2, x + 5, MOUTH_Y + h / 2), radius=5, fill=GREEN)
        elif expr == "thinking":
            dx = 14 * math.sin(now * 2.2)
            self._eyes(d, VIOLET, dx=dx, dy=-8, scale_y=0.9)
            for i in range(3):
                on = int(now * 3) % 3 >= i
                d.ellipse((cx - 30 + i * 24, MOUTH_Y - 6, cx - 18 + i * 24, MOUTH_Y + 6),
                          fill=VIOLET if on else (60, 52, 92))
        elif expr == "speaking":
            self._eyes(d, CYAN, closed=self._blink_amount(now))
            open_ = 0.25 + 0.75 * abs(math.sin(now * 9.0)) * (0.7 + 0.3 * math.sin(now * 2.3))
            h = 8 + 30 * open_
            d.rounded_rectangle((cx - 30, MOUTH_Y - h / 2, cx + 30, MOUTH_Y + h / 2), radius=min(14, h / 2), fill=CYAN)
        elif expr in ("happy", "excited"):
            color = AMBER if expr == "excited" and int(now * 4) % 2 else CYAN
            for x in EYE_X:   # ^ ^ eyes
                d.arc((x - 40, EYE_Y - 20, x + 40, EYE_Y + 50), 200, 340, fill=color, width=12)
            d.chord((cx - 44, MOUTH_Y - 36, cx + 44, MOUTH_Y + 20), 0, 180, fill=color)
            if expr == "excited":
                for i, (sx, sy) in enumerate(((70, 60), (410, 70), (95, 200), (390, 190))):
                    r = 5 + 4 * abs(math.sin(now * 4 + i))
                    d.line((sx - r, sy, sx + r, sy), fill=AMBER, width=3)
                    d.line((sx, sy - r, sx, sy + r), fill=AMBER, width=3)
        elif expr == "confused":
            self._eyes(d, CYAN, sizes=(1.0, 0.7))
            pts = [(cx - 36 + i * 9, MOUTH_Y + (6 if i % 2 else -6)) for i in range(9)]
            d.line(pts, fill=CYAN, width=5, joint="curve")
            d.text((EYE_X[1] + 60, EYE_Y - 70), "?", font=self.f_big, fill=AMBER)
        elif expr == "sleep":
            for x in EYE_X:
                d.rounded_rectangle((x - EYE_W / 2, EYE_Y - 3, x + EYE_W / 2, EYE_Y + 5), radius=4, fill=(40, 70, 95))
            d.rounded_rectangle((cx - 16, MOUTH_Y - 3, cx + 16, MOUTH_Y + 3), radius=3, fill=(40, 70, 95))
            for i in range(3):
                t = (now * 0.6 + i / 3) % 1.0
                d.text((EYE_X[1] + 70 + 18 * t, EYE_Y - 30 - 60 * t), "z", font=self.f_bold,
                       fill=tuple(int(c * (1 - t)) for c in MUTED))
        else:  # error
            self._eyes(d, RED, scale_y=0.35)
            d.arc((cx - 34, MOUTH_Y - 4, cx + 34, MOUTH_Y + 34), 200, 340, fill=RED, width=6)

    def _wrap(self, text: str, font, width: int, max_lines: int) -> list[str]:
        words, lines, cur = text.split(), [], ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if font.getlength(trial) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            cur = w
            if len(lines) == max_lines:
                break
        if cur and len(lines) < max_lines:
            lines.append(cur)
        if len(lines) == max_lines and " ".join(lines) != " ".join(words):
            last = lines[-1]
            while last and font.getlength(last + "...") > width:
                last = last[:-1]
            lines[-1] = last.rstrip() + "..."
        return lines

    def _caption(self, d: ImageDraw.ImageDraw, now: float) -> None:
        s = self.state
        if not s.caption or now > s.caption_until:
            return
        font = self.f_caption_ta if has_tamil(s.caption) else self.f_caption_lat
        prefix = "You: " if s.caption_role == "user" else ""
        color = MUTED if s.caption_role == "user" else (AMBER if s.caption_role == "system" else INK)
        lines = self._wrap(prefix + s.caption, font, self.size[0] - 28, 2)
        d.line((14, CAPTION_TOP - 4, self.size[0] - 14, CAPTION_TOP - 4), fill=(38, 48, 70), width=1)
        for i, line in enumerate(lines):
            d.text((14, CAPTION_TOP + 4 + i * 30), line, font=font, fill=color)

    def _info(self, d: ImageDraw.ImageDraw) -> None:
        d.text((16, 12), "AIRA status", font=self.f_big, fill=CYAN)
        for i, line in enumerate(self.state.info_lines[:8]):
            d.text((16, 62 + i * 30), line[:52], font=self.f_caption_lat, fill=INK)

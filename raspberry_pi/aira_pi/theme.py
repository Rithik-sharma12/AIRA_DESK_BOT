"""AIRA's mascot theme: the pixel-art girl from the concept board, on the 480x320 screen.

Layout (matches the concept board's home screen):

    +--------------------------------------------------------------+  y 0
    | <3 AIRA                                  wifi  mic   20:30   |  top bar
    +-----------------------------------+--------------------------+  y 36
    |                                   |  speech bubble           |
    |   mascot scene (300 x 240)        |  (AIRA's words, paged)   |
    |   one animated sprite per emotion |                          |
    |                                   |  live decoration         |
    +-----------------------------------+--------------------------+  y 280
    | CPU 12% | RAM 135/414 MB | 42°C                  ||||| ONLINE |  bottom bar
    +--------------------------------------------------------------+  y 320

Other screens: system status (button hold), clock / standby (laptop away) and a
notification toast ("Connected to ..."). Sprites come from assets/mascot/ (see its
README); everything else is drawn here, so text and numbers stay live and crisp.
``ThemeRenderer`` has the same interface as the simple ``FaceRenderer``.
"""

from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .face import TAMIL_FONTS, FaceState, _find_font, _font, has_tamil

W, H = 480, 320
ASSETS = Path(__file__).resolve().parent / "assets" / "mascot"
SCENE = (8, 38, 300, 240)          # x, y, w, h of the mascot sprite
RIGHT_X = 318                      # right column
BOTTOM_Y = 286

BG = (13, 8, 32)
PANEL = (22, 14, 48)
FRAME = (139, 92, 246)
LAVENDER = (196, 181, 253)
PINK = (217, 70, 239)
INK = (245, 243, 255)
MUTED = (165, 160, 201)
CYAN = (56, 189, 248)
GREEN = (52, 211, 153)
AMBER = (251, 191, 36)
RED = (244, 63, 94)

UNIFONT = ("unifont.otf", "unifont.ttf", "Unifont.otf")
EXPRESSION_TO_SPRITE = {"blinking": "idle", "sleep": "sleepy"}
DEFAULT_LINES = {
    "idle": "Hey! What are we working on?",
    "happy": "Yay!",
    "thinking": "Hmm...",
    "listening": "Listening...",
    "speaking": "",
    "excited": "That's awesome!",
    "confused": "Hmm? Let me check...",
    "sleepy": "Taking a short break...",
    "error": "Something went wrong. Let's check it.",
}

# Tiny pixel icons ("X" = lit pixel), drawn at an integer scale.
ICON_HEART = ["XX.XX", "XXXXX", "XXXXX", ".XXX.", "..X.."]
ICON_WIFI = ["..XXXXX..", ".X.....X.", "X..XXX..X", "..X...X..", "....X....", "........."]
ICON_MIC = [".XXX.", ".XXX.", ".XXX.", "XXXXX", "X.X.X", ".XXX.", "..X..", ".XXX."]
ICON_STAR = ["..X..", "..X..", "XXXXX", "..X..", "..X.."]


def draw_icon(d: ImageDraw.ImageDraw, icon: list[str], x: int, y: int, scale: int, color) -> None:
    for row, line in enumerate(icon):
        for col, ch in enumerate(line):
            if ch == "X":
                d.rectangle((x + col * scale, y + row * scale, x + (col + 1) * scale - 1,
                             y + (row + 1) * scale - 1), fill=color)


def pixel_box(d: ImageDraw.ImageDraw, box, fill, outline, width: int = 2, step: int = 4) -> None:
    """A rectangle with stepped (pixel-art) corners."""
    x0, y0, x1, y1 = box
    d.rectangle((x0 + step, y0, x1 - step, y1), fill=outline)
    d.rectangle((x0, y0 + step, x1, y1 - step), fill=outline)
    d.rectangle((x0 + step // 2, y0 + step // 2, x1 - step // 2, y1 - step // 2), fill=outline)
    i = width
    d.rectangle((x0 + step + i - 2, y0 + i, x1 - step - i + 2, y1 - i), fill=fill)
    d.rectangle((x0 + i, y0 + step + i - 2, x1 - i, y1 - step - i + 2), fill=fill)
    d.rectangle((x0 + step // 2 + i, y0 + step // 2 + i, x1 - step // 2 - i, y1 - step // 2 - i), fill=fill)


# --------------------------------------------------------------------- Pi stats
class PiStats:
    """CPU %, RAM, swap and temperature from /proc (refreshed at most every 2 s)."""

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None
        self._at = 0.0
        self.data: dict[str, float | None] = {"cpu": None, "ram_used": None, "ram_total": None,
                                              "swap_used": None, "swap_total": None, "temp": None}

    def refresh(self, now: float) -> dict:
        if now - self._at < 2.0:
            return self.data
        self._at = now
        try:
            fields = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
            idle, total = fields[3] + fields[4], sum(fields)
            if self._prev:
                dt, di = total - self._prev[1], idle - self._prev[0]
                self.data["cpu"] = round(100 * (1 - di / dt)) if dt else 0
            self._prev = (idle, total)
            mem = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) // 1024
            self.data["ram_total"] = mem.get("MemTotal")
            self.data["ram_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
            self.data["swap_total"] = mem.get("SwapTotal")
            self.data["swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        except (OSError, ValueError, IndexError):
            pass
        try:
            self.data["temp"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000)
        except (OSError, ValueError):
            pass
        return self.data


# ------------------------------------------------------------------- sprites
BLINK_FPS = 14


@dataclass
class Animation:
    frames: list[Image.Image]
    fps: float
    blink: list[Image.Image] | None = None    # played now and then over the loop (eyes only)

    def frame(self, now: float) -> Image.Image:
        if len(self.frames) == 1:
            return self.frames[0]
        return self.frames[int(now * self.fps) % len(self.frames)]


def _fit(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Full-scene frames: scale to fill the scene and centre-crop."""
    if img.size == size:
        return img
    scale = max(size[0] / img.width, size[1] / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    left, top = (img.width - size[0]) // 2, (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def _place(character: Image.Image, background: Image.Image) -> Image.Image:
    """Transparent character frames: scale to fit, stand her on the bottom edge of the background."""
    w, h = background.size
    scale = min(w / character.width, h / character.height)
    if scale != 1:
        resample = Image.Resampling.NEAREST if scale >= 1 and float(scale).is_integer() else Image.Resampling.LANCZOS
        character = character.resize((round(character.width * scale), round(character.height * scale)), resample)
    out = background.copy()
    out.paste(character, ((w - character.width) // 2, h - character.height), character)
    return out


def _load_frame(path: Path, size: tuple[int, int], background: Image.Image) -> Image.Image:
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "P") and "A" in img.convert("RGBA").getbands():
        rgba = img.convert("RGBA")
        if rgba.getextrema()[3][0] < 255:          # really has transparent pixels
            return _place(rgba, background)
    return _fit(img.convert("RGB"), size)


def load_sprites(folder: Path = ASSETS) -> dict[str, Animation]:
    """Frames listed in manifest.json. Either full 300x240 scenes, or transparent character
    frames (any size, e.g. 160x160) drawn over background.png (or a plain night colour)."""
    manifest = folder / "manifest.json"
    if not manifest.exists():
        return {}
    data = json.loads(manifest.read_text(encoding="utf-8"))
    size = (SCENE[2], SCENE[3])
    bg_file = folder / str(data.get("background", "background.png"))
    background = _fit(Image.open(bg_file).convert("RGB"), size) if bg_file.exists() else Image.new("RGB", size, PANEL)
    out: dict[str, Animation] = {}
    for name, spec in (data.get("emotions") or {}).items():
        cache: dict[str, Image.Image] = {}   # a frame listed twice (to hold it longer) is loaded once

        def load(names, name=name, cache=cache):
            out_frames = []
            for f in names:
                if f not in cache and (folder / name / f).exists():
                    cache[f] = _load_frame(folder / name / f, size, background)
                if f in cache:
                    out_frames.append(cache[f])
            return out_frames
        frames = load(spec.get("frames", []))
        if frames:
            out[name] = Animation(frames, float(spec.get("fps", 2)), load(spec.get("blink", [])) or None)
    return out


# ------------------------------------------------------------------ renderer
@dataclass
class ThemeState(FaceState):
    caption_since: float = 0.0
    notice: str = ""
    notice_until: float = 0.0
    host: str = ""


class ThemeRenderer:
    """Drop-in replacement for FaceRenderer that draws the mascot theme."""

    def __init__(self, sprites: dict[str, Animation] | None = None, size: tuple[int, int] = (W, H)):
        self.size = size
        self.sprites = sprites if sprites is not None else load_sprites()
        if "idle" not in self.sprites:
            raise FileNotFoundError(f"no mascot sprites in {ASSETS} (run scripts/make_mascot_sprites.py)")
        self.state = ThemeState()
        self.stats = PiStats()
        uni = _find_font(UNIFONT)
        self.f_px = ImageFont.truetype(uni, 16) if uni else _font(("DejaVuSansMono.ttf",), 14)
        self.f_ta = _font(TAMIL_FONTS, 15) if _find_font(TAMIL_FONTS) else self.f_px
        self.pixel_font = uni is not None
        self.ta_dy = -3       # Tamil font baseline vs the pixel font
        self._title = self._big_text("AIRA", 2, LAVENDER, bold=True)
        self._thumbs: dict[str, Image.Image] = {}
        self._rng = random.Random()
        self._next_blink = time.monotonic() + 3.0

    # --------------------------------------------------- FaceRenderer interface
    def set_expression(self, expression: str, hold_s: float | None = None, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.state.expression = expression
        self.state.hold_until = now + hold_s if hold_s else 0.0

    def set_caption(self, text: str, role: str = "aira", seconds: float = 12.0, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.state.caption = " ".join(text.split())[:400]
        self.state.caption_role = role
        self.state.caption_until = now + max(seconds, 6 + len(text) / 12)
        self.state.caption_since = now

    def show_info(self, lines: list[str], seconds: float = 10.0) -> None:
        self.state.info_lines = lines
        self.state.info_until = time.monotonic() + seconds

    def flash(self, seconds: float = 6.0) -> None:
        self.state.flash_until = time.monotonic() + seconds

    def notify(self, text: str, seconds: float = 3.5) -> None:
        self.state.notice = text[:40]
        self.state.notice_until = time.monotonic() + seconds

    def animating(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        sprite = self.sprites.get(self._sprite_name(self.state.expression))
        blink_soon = sprite is not None and sprite.blink and -0.6 < now - self._next_blink < 0.6
        return bool(self.state.expression in ("listening", "thinking", "speaking", "sleep", "excited")
                    or (sprite is not None and len(sprite.frames) > 1) or now < self.state.flash_until
                    or now < self.state.notice_until or blink_soon)

    # ----------------------------------------------------------------- render
    def render(self, now: float | None = None) -> Image.Image:
        now = time.monotonic() if now is None else now
        s = self.state
        if s.hold_until and now >= s.hold_until:
            s.expression, s.hold_until = "idle", 0.0
        img = Image.new("RGB", self.size, BG)
        d = ImageDraw.Draw(img)
        self.stats.refresh(now)
        if now < s.info_until:
            self._status_screen(img, d, now)
        elif not s.linked:
            self._clock_screen(img, d, now)
        else:
            expr = s.expression
            if now < s.flash_until and expr in ("idle", "speaking", "happy"):
                expr = "excited"
            self._home(img, d, expr, now)
        if now < s.notice_until:
            self._notice(d)
        return img

    # ---------------------------------------------------------------- pieces
    def _sprite_name(self, expression: str) -> str:
        name = EXPRESSION_TO_SPRITE.get(expression, expression)
        return name if name in self.sprites else "idle"

    def _big_text(self, text: str, scale: int, color, bold: bool = False) -> Image.Image:
        """Render pixel text at 16 px, then enlarge with nearest-neighbour for a chunky title."""
        w = int(self.f_px.getlength(text)) + 3
        small = Image.new("RGBA", (w, 18), (0, 0, 0, 0))
        sd = ImageDraw.Draw(small)
        for dx in ((0, 1) if bold else (0,)):
            sd.text((dx, 0), text, font=self.f_px, fill=color)
        return small.resize((w * scale, 18 * scale), Image.Resampling.NEAREST)

    def _runs(self, text: str) -> list[tuple[ImageFont.FreeTypeFont, str]]:
        """Split text into Tamil and non-Tamil runs: the Tamil font has no Latin letters and
        the pixel font cannot shape Tamil, so each run uses the font that can draw it."""
        runs: list[list] = []
        for ch in text:
            if ch.isspace() and runs:
                kind = runs[-1][0]
            else:
                kind = "ta" if (0x0B80 <= ord(ch) < 0x0C00 or ch in "\u200c\u200d") else "px"
            if runs and runs[-1][0] == kind:
                runs[-1][1] += ch
            else:
                runs.append([kind, ch])
        return [(self.f_ta if k == "ta" else self.f_px, s) for k, s in runs]

    def _measure(self, text: str) -> float:
        return sum(font.getlength(s) for font, s in self._runs(text))

    def _text(self, d, xy, text: str, color=INK, anchor: str | None = None) -> None:
        x, y = xy
        if anchor == "ra":
            x -= self._measure(text)
        elif anchor == "ma":
            x -= self._measure(text) / 2
        for font, s in self._runs(text):
            dy = self.ta_dy if font is self.f_ta and font is not self.f_px else 0
            d.text((x, y + dy), s, font=font, fill=color)
            x += font.getlength(s)

    def _top_bar(self, img, d, now: float, listening: bool = False) -> None:
        d.rectangle((0, 0, W - 1, 33), fill=PANEL)
        d.line((0, 33, W - 1, 33), fill=FRAME, width=2)
        beat = 1 if int(now * 1.2) % 4 == 0 else 0           # heart "beats" now and then
        draw_icon(d, ICON_HEART, 8, 11, 3, PINK if not beat else (244, 114, 182))
        img.paste(self._title, (30, 0), self._title)
        draw_icon(d, ICON_WIFI, 344, 11, 2, CYAN if self.state.linked else MUTED)
        draw_icon(d, ICON_MIC, 374, 9, 2, GREEN if listening else (MUTED if not self.state.linked else LAVENDER))
        self._text(d, (W - 12, 9), time.strftime("%H:%M"), INK, anchor="ra")

    def _bottom_bar(self, d, now: float) -> None:
        st = self.stats.data
        d.rectangle((0, BOTTOM_Y, W - 1, H - 1), fill=PANEL)
        d.line((0, BOTTOM_Y, W - 1, BOTTOM_Y), fill=FRAME, width=2)
        parts = []
        if st["cpu"] is not None:
            parts.append(f"CPU {st['cpu']}%")
        if st["ram_used"] is not None:
            parts.append(f"RAM {st['ram_used']}/{st['ram_total']}MB")
        if st["temp"] is not None:
            parts.append(f"{st['temp']}°C")
        self._text(d, (10, BOTTOM_Y + 9), " | ".join(parts) or "AIRA", MUTED)
        online = self.state.linked
        x = W - 12
        label = "ONLINE" if online else "OFFLINE"
        self._text(d, (x, BOTTOM_Y + 9), label, GREEN if online else AMBER, anchor="ra")
        lw = int(self.f_px.getlength(label))
        d.rectangle((x - lw - 14, BOTTOM_Y + 13, x - lw - 7, BOTTOM_Y + 20), fill=GREEN if online else AMBER)

    def _frame(self, name: str, now: float) -> Image.Image:
        """The sprite frame for now: the emotion's loop, with a natural blink every few seconds."""
        anim = self.sprites[name]
        if anim.blink:
            if now >= self._next_blink + len(anim.blink) / BLINK_FPS:
                self._next_blink = now + self._rng.uniform(2.5, 6.0)
                if self._rng.random() < 0.15:                 # sometimes a quick double blink
                    self._next_blink = now + 0.45
            k = int((now - self._next_blink) * BLINK_FPS)
            if 0 <= k < len(anim.blink):
                return anim.blink[k]
        return anim.frame(now)

    def _scene(self, img, d, name: str, now: float) -> None:
        x, y, w, h = SCENE
        img.paste(self._frame(name, now), (x, y))
        d.rectangle((x - 2, y - 2, x + w + 1, y + h + 1), outline=FRAME, width=2)

    def _wrap(self, text: str, width: int) -> list[str]:
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if self._measure(trial) <= width:
                cur = trial
                continue
            if cur:
                lines.append(cur)
            while self._measure(word) > width:  # a very long word: hard-break it
                cut = len(word)
                while cut > 1 and self._measure(word[:cut]) > width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            cur = word
        if cur:
            lines.append(cur)
        return lines

    def _bubble(self, d, text: str, role: str, now: float) -> int:
        """Speech bubble in the right column; long text pages every 4 s. Returns its bottom y."""
        x0, x1, y0 = RIGHT_X, W - 6, 40
        line_h = 20 if not has_tamil(text) else 22
        lines = self._wrap(text, x1 - x0 - 22)
        per_page = 8
        pages = max(1, math.ceil(len(lines) / per_page))
        page = int((now - self.state.caption_since) / 4) % pages if pages > 1 else 0
        shown = lines[page * per_page:(page + 1) * per_page]
        y1 = y0 + 18 + line_h * max(1, len(shown)) + (14 if pages > 1 else 0)
        pixel_box(d, (x0, y0, x1, y1), PANEL, LAVENDER if role != "system" else AMBER)
        # tail pointing at AIRA
        for i in range(6):
            d.rectangle((x0 - 8 + i, y0 + 22 + i, x0 + 1, y0 + 23 + i), fill=LAVENDER)
        d.rectangle((x0 - 2, y0 + 24, x0 + 2, y0 + 27), fill=PANEL)
        color = MUTED if role == "user" else (AMBER if role == "system" else INK)
        for i, line in enumerate(shown):
            self._text(d, (x0 + 11, y0 + 10 + i * line_h), line, color)
        if pages > 1:
            for p in range(pages):
                d.rectangle((x1 - 12 - (pages - 1 - p) * 8, y1 - 10, x1 - 8 - (pages - 1 - p) * 8, y1 - 6),
                            fill=LAVENDER if p == page else MUTED)
        return y1

    def _decoration(self, d, name: str, top: int, now: float) -> None:
        """Small live animation under the bubble, so AIRA looks alive between sprite frames."""
        bottom = SCENE[1] + SCENE[3] - 4
        cx = (RIGHT_X + W - 6) // 2
        if bottom - top < 30:
            return
        mid = (top + bottom) // 2
        if name in ("listening", "speaking"):
            color = GREEN if name == "listening" else LAVENDER
            for i in range(9):
                h = 6 + 26 * abs(math.sin(now * (7 if name == "speaking" else 5) + i * 0.9))
                x = cx - 44 + i * 11
                d.rectangle((x, mid - h / 2, x + 5, mid + h / 2), fill=color)
        elif name == "thinking":
            for i in range(3):
                on = int(now * 3) % 3 >= i
                d.rectangle((cx - 22 + i * 18, mid - 4, cx - 14 + i * 18, mid + 4), fill=LAVENDER if on else PANEL)
        elif name == "sleepy":
            for i in range(3):
                t = (now * 0.5 + i / 3) % 1.0
                c = tuple(int(v * (1 - t * 0.8)) for v in LAVENDER)
                self._text(d, (cx - 20 + i * 14, bottom - 10 - int(t * (bottom - top - 20))), "z" if i % 2 else "Z", c)
        elif name in ("excited", "happy"):
            for i, (dx, dy) in enumerate(((-50, -10), (40, -18), (-20, 14), (55, 16))):
                if int(now * 3 + i) % 3:
                    draw_icon(d, ICON_STAR, cx + dx, mid + dy, 2, AMBER if name == "excited" else PINK)
        elif name == "error":
            self._text(d, (cx, mid - 8), "check the laptop", RED, anchor="ma")
        else:
            if int(now) % 6 < 3:
                draw_icon(d, ICON_HEART, cx - 7, mid - 5, 3, PINK)

    def _home(self, img, d, expr: str, now: float) -> None:
        name = self._sprite_name(expr)
        self._top_bar(img, d, now, listening=(name == "listening"))
        self._scene(img, d, name, now)
        s = self.state
        if s.caption and now <= s.caption_until:
            text, role = (("You: " + s.caption) if s.caption_role == "user" else s.caption), s.caption_role
        else:
            text, role = DEFAULT_LINES.get(name, ""), "aira"
        if name == "listening":
            text, role = "Listening...", "aira"
        bottom = self._bubble(d, text, role, now) if text else 40
        self._decoration(d, name, bottom + 6, now)
        self._bottom_bar(d, now)

    def _thumb(self, name: str, size: tuple[int, int]) -> Image.Image:
        key = f"{name}{size}"
        if key not in self._thumbs:
            self._thumbs[key] = _fit(self.sprites[self._sprite_name(name)].frames[0], size)
        return self._thumbs[key]

    def _status_screen(self, img, d, now: float) -> None:
        self._top_bar(img, d, now)
        img.paste(self._thumb("happy", (150, 200)), (8, 44))
        d.rectangle((6, 42, 159, 245), outline=FRAME, width=2)
        st = self.stats.data
        x, y = 172, 44
        rows = []
        if st["cpu"] is not None:
            rows.append(("CPU", st["cpu"] / 100, f"{st['cpu']}%"))
        if st["ram_total"]:
            rows.append(("RAM", st["ram_used"] / st["ram_total"], f"{st['ram_used']}/{st['ram_total']}MB"))
        if st["swap_total"]:
            rows.append(("SWAP", st["swap_used"] / st["swap_total"], f"{st['swap_used']}/{st['swap_total']}MB"))
        if st["temp"] is not None:
            rows.append(("TEMP", min(1.0, st["temp"] / 85), f"{st['temp']}°C"))
        for label, frac, value in rows:
            self._text(d, (x, y), label, MUTED)
            d.rectangle((x + 48, y + 3, x + 168, y + 13), fill=PANEL, outline=FRAME)
            d.rectangle((x + 49, y + 4, x + 49 + int(118 * max(0.0, min(1.0, frac))), y + 12),
                        fill=RED if frac > 0.85 else LAVENDER)
            self._text(d, (W - 10, y), value, INK, anchor="ra")
            y += 24
        y += 6
        for line in self.state.info_lines[:6]:
            for part in self._wrap(line, W - x - 10)[:1]:
                self._text(d, (x, y), part, INK if ":" not in part else LAVENDER)
            y += 20
        self._bottom_bar(d, now)

    def _clock_screen(self, img, d, now: float) -> None:
        self._top_bar(img, d, now)
        img.paste(self._thumb("sleepy", (180, 200)), (8, 44))
        d.rectangle((6, 42, 189, 245), outline=FRAME, width=2)
        clock = self._big_text(time.strftime("%H:%M"), 4, INK)
        cx = (200 + W) // 2
        img.paste(clock, (cx - clock.width // 2, 70), clock)
        self._text(d, (cx, 150), time.strftime("%a, %d %b %Y"), MUTED, anchor="ma")
        draw_icon(d, ICON_HEART, cx - 70, 188, 2, PINK)
        msg = self.state.status_text or "See you soon..."
        self._text(d, (cx - 52, 184), msg[:22], LAVENDER)
        if self.state.caption and now <= self.state.caption_until:
            for i, line in enumerate(self._wrap(self.state.caption, W - 210)[:3]):
                self._text(d, (204, 214 + i * 20), line, AMBER if self.state.caption_role == "system" else INK)
        self._bottom_bar(d, now)

    def _notice(self, d) -> None:
        text = self.state.notice
        w = max(220, int(self._measure(text)) + 100)
        x0, y0 = (W - w) // 2, 120
        pixel_box(d, (x0, y0, x0 + w, y0 + 56), PANEL, LAVENDER)
        d.ellipse((x0 + 14, y0 + 14, x0 + 42, y0 + 42), fill=(59, 130, 246))
        self._text(d, (x0 + 28, y0 + 19), "i", INK, anchor="ma")
        self._text(d, (x0 + 54, y0 + 20), text, INK)
        d.line((x0 + w - 32, y0 + 29, x0 + w - 25, y0 + 36, x0 + w - 14, y0 + 21), fill=GREEN, width=3)


def create_renderer(theme: str = "auto"):
    """'brutal' (design system + touch menu, the default), 'mascot' (night pixel theme) or
    'simple' (vector eyes). Without sprites, everything falls back to 'simple'."""
    from .face import FaceRenderer

    if theme in ("auto", "brutal", "mascot"):
        try:
            if theme == "mascot":
                return ThemeRenderer()
            from .ui_brutal import BrutalRenderer

            return BrutalRenderer()
        except (FileNotFoundError, OSError, ValueError):
            if theme != "auto":
                raise
    return FaceRenderer()

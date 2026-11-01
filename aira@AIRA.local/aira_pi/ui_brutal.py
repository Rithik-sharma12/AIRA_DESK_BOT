"""AIRA's Pi screen in the neo-brutalist design system (same look as the web page).

Thick black strokes, squared corners, hard shadows, teal / magenta / yellow on paper beige,
and the pixel-art mascot. A touch menu on the left (Home, Chat, Music, Tools, System),
as in the concept board:

    +------+------------------------------------------------------+
    | HOME |  AIRA [READY]                          (o) 20:30     |
    | CHAT |------------------------------------------------------|
    | MUSIC|  +--------------------+  +-----------------------+   |
    | TOOLS|  |  mascot (blinks,   |  | speech bubble          |   |
    | SYS  |  |  talks)            |  |                        |   |
    |      |  +--------------------+  +-----------------------+   |
    |      |  [CPU 12%] [RAM 135/414] [42°C]          [ONLINE]   |
    +------+------------------------------------------------------+

Every touchable area is registered while drawing, so ``tap(x, y)`` returns exactly what
is on screen: ``"say:<text>"`` (send to AIRA), ``"sys:<action>"`` or None (handled here,
e.g. switching screens). Without touch, the button still works and the screen returns to
Home by itself.
"""

from __future__ import annotations

import math
import time
from collections import deque

from PIL import Image, ImageDraw

from .face import TAMIL_FONTS, _find_font, _font
from .theme import ThemeRenderer

W, H = 480, 320
TEAL, MAGENTA, YELLOW = (0, 194, 203), (255, 0, 255), (255, 215, 0)
BLACK, WHITE, PAPER = (0, 0, 0), (255, 255, 255), (245, 245, 220)
MUTED = (74, 74, 64)
NIGHT = (13, 8, 32)

RAIL = 60
X0, X1 = 68, W - 6
TOP = 46
SCREENS = ("home", "chat", "music", "tools", "system")
STATE_TAG = {"idle": ("READY", WHITE), "listening": ("LISTENING", TEAL), "thinking": ("THINKING", YELLOW),
             "speaking": ("SPEAKING", WHITE), "error": ("ERROR", MAGENTA), "sleep": ("ASLEEP", WHITE)}
MOOD_TAG = {"happy": "DONE!", "excited": "REMINDER", "confused": "HMM?"}

MUSIC = [("PREV", "previous song", "prev"), ("PLAY / PAUSE", "pause the music", "play"), ("NEXT", "next song", "next"),
         ("VOL -", "volume down", "voldown"), ("MUTE", "mute", "mute"), ("VOL +", "volume up", "volup")]
TOOLS = [("LAPTOP STATUS", "How is my laptop doing?"), ("TIMER 5 MIN", "set a timer for 5 minutes"),
         ("REMINDERS", "what reminders do I have?"), ("LOCK LAPTOP", "lock the laptop"),
         ("OPEN CHROME", "open chrome"), ("OPEN VS CODE", "open vs code")]
SYSTEM_ACTIONS = [("TEXT CONSOLE", "console", WHITE), ("RESTART PI", "reboot", YELLOW), ("SHUT DOWN PI", "poweroff", MAGENTA)]
CONFIRM_S = 4.0
HOME_AFTER_S = 30.0


class BrutalRenderer(ThemeRenderer):
    def __init__(self, sprites=None, size=(W, H)):
        super().__init__(sprites, size)
        bold = ("NotoSans-Bold.ttf", "DejaVuSans-Bold.ttf")
        self.f_px = _font(bold, 15)                    # body text (name kept: shared text helpers use it)
        self.f_small = _font(bold, 12)
        self.f_tag = _font(bold, 11)
        self.f_title = _font(bold, 24)
        self.f_huge = _font(bold, 64)
        self.f_ta = _font(("NotoSansTamil-Bold.ttf", *TAMIL_FONTS), 15) if _find_font(TAMIL_FONTS) else self.f_px
        self.ta_dy = 0
        self.screen = "home"
        self.last_touch = 0.0
        self.history: deque[tuple[str, str]] = deque(maxlen=12)
        self._hits: list[tuple[tuple[int, int, int, int], str]] = []
        self._pressed: tuple[str, float] | None = None
        self._armed: tuple[str, float] | None = None
        self._scaled: dict[int, Image.Image] = {}

    # ----------------------------------------------------------- interface
    def set_caption(self, text: str, role: str = "aira", seconds: float = 12.0, now: float | None = None) -> None:
        super().set_caption(text, role, seconds, now)
        clean = " ".join(text.split())
        if role in ("user", "aira") and clean and (not self.history or self.history[-1] != (role, clean)):
            self.history.append((role, clean[:300]))
        # a new answer from AIRA brings you back to her, unless you are busy on another screen
        now = time.monotonic() if now is None else now
        if role == "aira" and self.screen != "home" and now - self.last_touch > 8:
            self.screen = "home"

    def show_info(self, lines: list[str], seconds: float = 12.0) -> None:
        super().show_info(lines, seconds)
        self.screen = "system"
        self.last_touch = time.monotonic()

    def animating(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        busy = self._pressed is not None and now - self._pressed[1] < 0.3
        return bool(super().animating(now) or busy or (self._armed and now - self._armed[1] < CONFIRM_S))

    def tap(self, x: int, y: int, now: float | None = None) -> str | None:
        """What is under the finger. Screen switches and confirmations are handled here."""
        now = time.monotonic() if now is None else now
        self.last_touch = now
        for (x0, y0, x1, y1), action in reversed(self._hits):
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._pressed = (action, now)
                if action.startswith("nav:"):
                    self.screen = action[4:]
                    self.state.info_until = 0.0
                    return None
                if action.startswith("sys:") and action != "sys:console":
                    if self._armed and self._armed[0] == action and now - self._armed[1] < CONFIRM_S:
                        self._armed = None
                        return action
                    self._armed = (action, now)          # risky: needs a second tap
                    return None
                return action
        return None

    # -------------------------------------------------------------- render
    def render(self, now: float | None = None) -> Image.Image:
        now = time.monotonic() if now is None else now
        s = self.state
        if s.hold_until and now >= s.hold_until:
            s.expression, s.hold_until = "idle", 0.0
        if self.screen != "home" and now - self.last_touch > HOME_AFTER_S and now >= s.info_until:
            self.screen = "home"
        self.stats.refresh(now)
        img = Image.new("RGB", self.size, PAPER)
        d = ImageDraw.Draw(img)
        self._hits = []
        self._rail(d, now)
        expr = s.expression
        if now < s.flash_until and expr in ("idle", "speaking", "happy"):
            expr = "excited"
        self._top(d, expr, now)
        {"home": self._home_screen, "chat": self._chat_screen, "music": self._music_screen,
         "tools": self._tools_screen, "system": self._system_screen}[self.screen](img, d, expr, now)
        if now < s.notice_until:
            self._toast(d, s.notice)
        return img

    # -------------------------------------------------------------- pieces
    def box(self, d, rect, fill=WHITE, shadow: int = 4, width: int = 3) -> None:
        x0, y0, x1, y1 = rect
        if shadow:
            d.rectangle((x0 + shadow, y0 + shadow, x1 + shadow, y1 + shadow), fill=BLACK)
        d.rectangle(rect, fill=fill, outline=BLACK, width=width)

    def tag(self, d, x: int, y: int, text: str, fill, anchor_right: bool = False) -> int:
        w = int(self.f_tag.getlength(text)) + 12
        if anchor_right:
            x -= w
        d.rectangle((x, y, x + w, y + 20), fill=fill, outline=BLACK, width=2)
        d.text((x + 6, y + 4), text, font=self.f_tag, fill=BLACK)
        return w

    def button(self, d, rect, label: str, action: str, fill=WHITE, now: float = 0.0, icon: str | None = None) -> None:
        pressed = self._pressed and self._pressed[0] == action and now - self._pressed[1] < 0.25
        armed = self._armed and self._armed[0] == action and now - self._armed[1] < CONFIRM_S
        x0, y0, x1, y1 = rect
        if pressed:                                   # pressed: slides into its shadow
            x0, y0, x1, y1 = x0 + 4, y0 + 4, x1 + 4, y1 + 4
            self.box(d, (x0, y0, x1, y1), fill, shadow=0)
        else:
            self.box(d, rect, fill)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        if armed:
            label = "TAP AGAIN"
        if icon:
            self._icon(d, icon, cx, cy - 8)
            d.text((cx, y1 - 18), label, font=self.f_small, fill=BLACK, anchor="ma")
        else:
            d.text((cx, cy), label, font=self.f_small, fill=BLACK, anchor="mm")
        self._hits.append((rect, action))

    def _icon(self, d, name: str, cx: int, cy: int) -> None:
        k = BLACK
        if name == "home":
            d.polygon([(cx - 11, cy), (cx, cy - 10), (cx + 11, cy)], outline=k, width=3)
            d.rectangle((cx - 8, cy, cx + 8, cy + 10), outline=k, width=3)
        elif name == "chat":
            d.rectangle((cx - 11, cy - 9, cx + 11, cy + 5), outline=k, width=3)
            d.polygon([(cx - 6, cy + 5), (cx - 6, cy + 11), (cx, cy + 5)], fill=k)
        elif name == "music":
            d.line((cx + 5, cy - 10, cx + 5, cy + 6), fill=k, width=3)
            d.line((cx + 5, cy - 10, cx + 11, cy - 7), fill=k, width=3)
            d.ellipse((cx - 5, cy + 2, cx + 5, cy + 10), fill=k)
        elif name == "tools":
            for i in range(2):
                for j in range(2):
                    d.rectangle((cx - 10 + i * 12, cy - 10 + j * 12, cx - 2 + i * 12, cy - 2 + j * 12), outline=k, width=3)
        elif name == "system":
            for i, (y, x) in enumerate(((-7, 4), (0, -4), (7, 2))):
                d.line((cx - 11, cy + y, cx + 11, cy + y), fill=k, width=3)
                d.rectangle((cx + x - 3, cy + y - 3, cx + x + 3, cy + y + 3), fill=PAPER, outline=k, width=2)
        elif name == "prev":
            d.polygon([(cx + 2, cy - 8), (cx + 2, cy + 8), (cx - 8, cy)], fill=k)
            d.polygon([(cx + 12, cy - 8), (cx + 12, cy + 8), (cx + 2, cy)], fill=k)
        elif name == "next":
            d.polygon([(cx - 2, cy - 8), (cx - 2, cy + 8), (cx + 8, cy)], fill=k)
            d.polygon([(cx - 12, cy - 8), (cx - 12, cy + 8), (cx - 2, cy)], fill=k)
        elif name == "play":
            d.polygon([(cx - 12, cy - 9), (cx - 12, cy + 9), (cx + 1, cy)], fill=k)
            d.rectangle((cx + 4, cy - 9, cx + 7, cy + 9), fill=k)
            d.rectangle((cx + 10, cy - 9, cx + 13, cy + 9), fill=k)
        elif name in ("voldown", "volup", "mute"):
            d.polygon([(cx - 12, cy - 4), (cx - 6, cy - 4), (cx, cy - 10), (cx, cy + 10), (cx - 6, cy + 4),
                       (cx - 12, cy + 4)], fill=k)
            if name == "mute":
                d.line((cx + 4, cy - 6, cx + 13, cy + 6), fill=k, width=3)
                d.line((cx + 4, cy + 6, cx + 13, cy - 6), fill=k, width=3)
            else:
                d.line((cx + 5, cy, cx + 13, cy), fill=k, width=3)
                if name == "volup":
                    d.line((cx + 9, cy - 4, cx + 9, cy + 4), fill=k, width=3)

    def _rail(self, d, now: float) -> None:
        d.rectangle((0, 0, RAIL - 4, H), fill=WHITE)
        d.line((RAIL - 4, 0, RAIL - 4, H), fill=BLACK, width=3)
        for i, name in enumerate(SCREENS):
            rect = (7, 7 + i * 62, RAIL - 13, 7 + i * 62 + 48)
            active = self.screen == name
            fill = TEAL if active else WHITE
            x0, y0, x1, y1 = rect
            d.rectangle((x0 + 3, y0 + 3, x1 + 3, y1 + 3), fill=BLACK)
            d.rectangle(rect, fill=fill, outline=BLACK, width=3)
            self._icon(d, name, (x0 + x1) // 2, (y0 + y1) // 2)
            self._hits.append(((0, y0 - 6, RAIL, y1 + 6), f"nav:{name}"))

    def _top(self, d, expr: str, now: float) -> None:
        d.text((X0, 6), "AIRA", font=self.f_title, fill=BLACK, stroke_width=1, stroke_fill=BLACK)
        x = X0 + int(self.f_title.getlength("AIRA")) + 12
        if self.screen == "home" or self.screen == "chat":
            name = self._sprite_name(expr)
            if self.state.expression == "idle" and name in MOOD_TAG:
                label, fill = MOOD_TAG[name], YELLOW if name != "happy" else TEAL
            else:
                label, fill = STATE_TAG.get(self.state.expression, ("READY", WHITE))
            self.tag(d, x, 12, label, fill)
        else:
            self.tag(d, x, 12, self.screen.upper(), YELLOW)
        d.text((X1, 10), time.strftime("%H:%M"), font=self.f_px, fill=BLACK, anchor="ra")
        tw = int(self.f_px.getlength("00:00"))
        d.rectangle((X1 - tw - 22, 15, X1 - tw - 10, 27), fill=TEAL if self.state.linked else YELLOW,
                    outline=BLACK, width=2)
        d.line((X0, TOP - 4, X1, TOP - 4), fill=BLACK, width=3)

    def _mascot(self, name: str, now: float, size=(226, 180)) -> Image.Image:
        frame = self._frame(name, now)
        key = (id(frame), size)
        img = self._scaled.get(key)
        if img is None:
            img = frame.resize(size, Image.Resampling.LANCZOS)
            self._scaled[key] = img
        return img

    def _stats_row(self, d, y: int) -> None:
        st = self.stats.data
        x = X0
        for text in ([f"CPU {st['cpu']}%"] if st["cpu"] is not None else []) + \
                    ([f"RAM {st['ram_used']}/{st['ram_total']}"] if st["ram_used"] is not None else []) + \
                    ([f"{st['temp']}°C"] if st["temp"] is not None else []):
            x += self.tag(d, x, y, text, WHITE) + 6
        online = self.state.linked
        self.tag(d, X1, y, "ONLINE" if online else "OFFLINE", TEAL if online else YELLOW, anchor_right=True)

    def _bubble(self, d, rect, text: str, role: str, now: float) -> None:
        x0, y0, x1, y1 = rect
        fill = YELLOW if role == "user" else (MAGENTA if role == "system" else WHITE)
        lines = self._wrap(text, x1 - x0 - 20)
        line_h = 21
        per_page = max(1, (y1 - y0 - 34) // line_h)
        pages = max(1, math.ceil(len(lines) / per_page))
        page = int((now - self.state.caption_since) / 4) % pages if pages > 1 else 0
        shown = lines[page * per_page:(page + 1) * per_page]
        bottom = min(y1, y0 + 30 + line_h * max(1, len(shown)) + (10 if pages > 1 else 0))
        self.box(d, (x0, y0, x1, bottom), fill)
        d.text((x0 + 10, y0 + 7), "YOU" if role == "user" else ("NOTICE" if role == "system" else "AIRA"),
               font=self.f_tag, fill=BLACK)
        for i, line in enumerate(shown):
            self._text(d, (x0 + 10, y0 + 24 + i * line_h), line, BLACK)
        if pages > 1:
            for p in range(pages):
                px = x1 - 10 - (pages - p) * 9
                d.rectangle((px, bottom - 10, px + 6, bottom - 5), fill=BLACK if p == page else MUTED)

    # -------------------------------------------------------------- screens
    def _home_screen(self, img, d, expr: str, now: float) -> None:
        s = self.state
        if not s.linked:
            self._standby(img, d, now)
            return
        name = self._sprite_name(expr)
        d.rectangle((X0 + 6, TOP + 8, X0 + 6 + 232, TOP + 8 + 186), fill=BLACK)
        d.rectangle((X0, TOP + 2, X0 + 231, TOP + 2 + 185), fill=NIGHT, outline=BLACK, width=3)
        img.paste(self._mascot(name, now), (X0 + 3, TOP + 5))
        if name == "listening":
            for i in range(5):                                  # live level bars on her card
                h = 6 + 14 * abs(math.sin(now * 6 + i))
                d.rectangle((X0 + 12 + i * 9, TOP + 176 - h, X0 + 17 + i * 9, TOP + 176), fill=TEAL, outline=BLACK)
        if s.caption and now <= s.caption_until:
            text = s.caption
            role = s.caption_role
        else:
            text = {"listening": "Listening...", "thinking": "Hmm, let me think...", "sleepy": "Taking a short break...",
                    "error": "Something went wrong. Let's check it."}.get(name, "Hey! What are we working on?")
            role = "aira"
        self._bubble(d, (X0 + 246, TOP + 2, X1 - 4, TOP + 186), text, role, now)
        self._stats_row(d, H - 34)

    def _standby(self, img, d, now: float) -> None:
        d.rectangle((X0 + 6, TOP + 8, X0 + 6 + 170, TOP + 8 + 186), fill=BLACK)
        d.rectangle((X0, TOP + 2, X0 + 169, TOP + 2 + 185), fill=NIGHT, outline=BLACK, width=3)
        img.paste(self._mascot("sleepy", now, (164, 180)), (X0 + 3, TOP + 5))
        cx = (X0 + 180 + X1) // 2
        d.text((cx, TOP + 30), time.strftime("%H:%M"), font=self.f_huge, fill=BLACK, anchor="ma",
               stroke_width=1, stroke_fill=BLACK)
        d.text((cx, TOP + 104), time.strftime("%a, %d %b %Y").upper(), font=self.f_small, fill=MUTED, anchor="ma")
        msg = (self.state.status_text or "see you soon").upper()
        w = int(self.f_tag.getlength(msg)) + 12
        self.tag(d, cx - w // 2, TOP + 130, msg, YELLOW)
        if self.state.caption and now <= self.state.caption_until:
            for i, line in enumerate(self._wrap(self.state.caption, X1 - X0 - 190)[:2]):
                self._text(d, (X0 + 186, TOP + 160 + i * 18), line, BLACK)
        self._stats_row(d, H - 34)

    def _chat_screen(self, img, d, expr: str, now: float) -> None:
        if not self.history:
            self.box(d, (X0, TOP + 10, X1 - 4, TOP + 90))
            d.text((X0 + 14, TOP + 26), "NO MESSAGES YET", font=self.f_px, fill=BLACK)
            d.text((X0 + 14, TOP + 52), "Talk to AIRA and the conversation shows here.", font=self.f_small, fill=MUTED)
            return
        y = H - 8
        avail = X1 - X0 - 50
        for role, text in reversed(self.history):
            lines = self._wrap(text, avail - 20)[:4]
            h = 32 + 21 * len(lines)
            if y - h < TOP + 4:
                break
            y0 = y - h
            if role == "user":
                x1 = X1 - 4
                x0 = max(X0 + 40, x1 - max(self._measure(line) for line in lines) - 26)
                fill = YELLOW
            else:
                x0 = X0
                x1 = min(X1 - 40, x0 + max(self._measure(line) for line in lines) + 26)
                fill = WHITE
            self.box(d, (int(x0), y0, int(x1), y - 4), fill, shadow=3)
            d.text((x0 + 9, y0 + 5), "YOU" if role == "user" else "AIRA", font=self.f_tag, fill=BLACK)
            for i, line in enumerate(lines):
                self._text(d, (x0 + 9, y0 + 20 + i * 21), line, BLACK)
            y = y0 - 6

    def _grid(self, d, items, now: float, cols: int = 3, top: int = TOP + 4, h: int = 72, gap: int = 12) -> None:
        w = (X1 - X0 - 6 - gap * (cols - 1)) // cols
        for i, item in enumerate(items):
            r, c = divmod(i, cols)
            x0 = X0 + c * (w + gap)
            y0 = top + r * (h + gap)
            label, say, *icon = item
            fill = TEAL if (icon and icon[0] == "play") else WHITE
            self.button(d, (x0, y0, x0 + w, y0 + h), label, f"say:{say}", fill, now, icon[0] if icon else None)

    def _music_screen(self, img, d, expr: str, now: float) -> None:
        self._grid(d, MUSIC, now, h=86)
        note = "Controls the laptop's music and volume" if self.state.linked else "Laptop not connected"
        d.text((X0, H - 30), note.upper(), font=self.f_tag, fill=MUTED)

    def _tools_screen(self, img, d, expr: str, now: float) -> None:
        self._grid(d, TOOLS, now, cols=2, h=70, gap=12)

    def _system_screen(self, img, d, expr: str, now: float) -> None:
        st = self.stats.data
        rows = []
        if st["cpu"] is not None:
            rows.append(("CPU", st["cpu"] / 100, f"{st['cpu']}%"))
        if st["ram_total"]:
            rows.append(("RAM", st["ram_used"] / st["ram_total"], f"{st['ram_used']}/{st['ram_total']} MB"))
        if st["swap_total"]:
            rows.append(("SWAP", st["swap_used"] / st["swap_total"], f"{st['swap_used']}/{st['swap_total']} MB"))
        if st["temp"] is not None:
            rows.append(("TEMP", min(1.0, st["temp"] / 85), f"{st['temp']}°C"))
        y = TOP + 4
        for label, frac, value in rows:
            d.text((X0, y + 1), label, font=self.f_tag, fill=BLACK)
            d.rectangle((X0 + 44, y, X0 + 200, y + 14), fill=WHITE, outline=BLACK, width=2)
            frac = max(0.0, min(1.0, frac))
            if frac > 0:
                d.rectangle((X0 + 46, y + 2, X0 + 46 + int(152 * frac), y + 12), fill=MAGENTA if frac > 0.85 else TEAL)
            d.text((X0 + 208, y + 1), value, font=self.f_tag, fill=BLACK)
            y += 22
        info = self.state.info_lines or []
        for i, line in enumerate(info[:6]):
            d.text((X0, y + 6 + i * 17), line[:58], font=self.f_tag, fill=BLACK if ":" in line else MUTED)
        top = H - 70
        w = (X1 - X0 - 6 - 2 * 10) // 3
        for i, (label, action, fill) in enumerate(SYSTEM_ACTIONS):
            x0 = X0 + i * (w + 10)
            self.button(d, (x0, top, x0 + w, top + 54), label, f"sys:{action}", fill, now)

    def _toast(self, d, text: str) -> None:
        w = max(220, int(self._measure(text)) + 70)
        x0 = X0 + (X1 - X0 - w) // 2
        y0 = 120
        self.box(d, (x0, y0, x0 + w, y0 + 52), YELLOW, shadow=6)
        d.rectangle((x0 + 12, y0 + 14, x0 + 36, y0 + 38), fill=BLACK)
        d.text((x0 + 24, y0 + 26), "i", font=self.f_px, fill=YELLOW, anchor="mm")
        self._text(d, (x0 + 48, y0 + 16), text, BLACK)

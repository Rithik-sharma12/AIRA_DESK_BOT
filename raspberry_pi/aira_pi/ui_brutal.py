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
SCREENS = ("home", "chat", "music", "tools", "emotions", "about", "system")
ROW = 45                     # menu pitch: 7 buttons, each with a full 45 px touch row (44 px minimum)
EMOTIONS = [("idle", "IDLE", "Waiting for you"), ("happy", "HAPPY", "A task went well"),
            ("excited", "EXCITED", "A reminder or a timer!"), ("listening", "LISTEN", "Hearing what you say"),
            ("thinking", "THINK", "Working on an answer"), ("speaking", "SPEAK", "Talking: her mouth moves"),
            ("confused", "CONFUSED", "Did not understand"), ("sleepy", "SLEEPY", "Quiet for a while"),
            ("error", "ERROR", "Something went wrong")]
EMOTION_S = 3.0              # auto-play: each emotion this long
EMO_PREVIEW = (180, 144)     # 0.6x the 300x240 sprites
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
# AIRA's picture: 20 % larger than the first layout (226x180), and exactly 0.9x the 300x240 sprites,
# so the scaled pixel art stays crisp. Standby shows a taller crop of her (same height).
MASCOT = (271, 216)
STANDBY = (197, 216)


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
        self.screen_until = 0.0          # a screen chosen from the web page stays this long
        self.history: deque[tuple[str, str]] = deque(maxlen=12)
        self._hits: list[tuple[tuple[int, int, int, int], str]] = []
        self._pressed: tuple[str, float] | None = None
        self._armed: tuple[str, float] | None = None
        self._scaled: dict[tuple, Image.Image] = {}
        self.emotion = "happy"                  # Emotions screen: the one being shown
        self.emotion_auto = False               # play through all of them
        self.emotion_since = 0.0
        # Joystick / keyboard navigation: a focus ring on the menu ("rail") or on a button ("content").
        self.focus_zone: str | None = None      # None = hidden until the controller is used
        self.focus_action: str | None = None    # the focused button, by its action ("emo:happy", ...)
        self.focus_at = 0.0
        self.about_info: dict = {"edition": "AIRA Half", "touch": ""}
        self._about_rows: tuple[float, list] = (-1e9, [])

    def prewarm(self) -> None:
        """Scale every frame once, so the first blink or word never stutters on the Pi Zero.
        The app runs this in a background thread at start-up (a few seconds on the Pi)."""
        for name, anim in self.sprites.items():
            for frame in [*anim.frames, *(anim.blink or [])]:
                self._fit(frame, MASCOT)
                if name == "sleepy":
                    self._fit(frame, STANDBY)

    # ----------------------------------------------------------- interface
    def set_caption(self, text: str, role: str = "aira", seconds: float = 12.0, now: float | None = None) -> None:
        super().set_caption(text, role, seconds, now)
        clean = " ".join(text.split())
        if role in ("user", "aira") and clean and (not self.history or self.history[-1] != (role, clean)):
            self.history.append((role, clean[:300]))
        # a new answer from AIRA brings you back to her, unless you are busy on another screen
        now = time.monotonic() if now is None else now
        if role == "aira" and self.screen != "home" and now - self.last_touch > 8 and now >= self.screen_until:
            self.screen = "home"

    def show_screen(self, name: str, seconds: float = 60.0, now: float | None = None) -> bool:
        """Switch screens from the laptop's web page (no touch needed); stays ``seconds``, then Home."""
        if name not in SCREENS:
            return False
        now = time.monotonic() if now is None else now
        self.screen = name
        self.screen_until = now + seconds if name != "home" else 0.0
        self.state.info_until = 0.0
        if name == "emotions":                    # no touch needed: show every emotion in turn
            self.emotion_auto, self.emotion_since = True, now
        return True

    def show_info(self, lines: list[str], seconds: float = 12.0) -> None:
        super().show_info(lines, seconds)
        self.screen = "system"
        self.last_touch = time.monotonic()

    def animating(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        busy = (self._pressed is not None and now - self._pressed[1] < 0.3) or self.screen == "emotions"
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
                if action == "emo:auto":
                    self.emotion_auto, self.emotion_since = not self.emotion_auto, now
                    return None
                if action.startswith("emo:"):
                    self.emotion, self.emotion_auto = action[4:], False
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
        if self.screen != "home" and now - self.last_touch > HOME_AFTER_S and now >= s.info_until \
                and now >= self.screen_until:
            self.screen = "home"
        self.stats.refresh(now)
        img = Image.new("RGB", self.size, PAPER)
        d = ImageDraw.Draw(img)
        self._hits = []
        self._rail(d, now)
        expr = s.expression
        if now < s.flash_until and expr in ("idle", "speaking", "happy"):
            expr = "excited"
        if not (self.screen == "home" and s.linked):      # Home has its own side panel instead
            self._top(d, expr, now)
        {"home": self._home_screen, "chat": self._chat_screen, "music": self._music_screen,
         "tools": self._tools_screen, "emotions": self._emotions_screen, "about": self._about_screen,
         "system": self._system_screen}[self.screen](img, d, expr, now)
        self._draw_focus(d, now)
        if now < s.notice_until:
            self._toast(d, s.notice)
        return img

    # ------------------------------------------------------- joystick / keys
    FOCUS_S = 30.0                                  # the ring hides after this long without input

    def _content_hits(self) -> list[tuple[tuple[int, int, int, int], str]]:
        return [(r, a) for r, a in self._hits if not a.startswith("nav:")]

    def navigate(self, direction: str, now: float | None = None) -> None:
        """Move the focus: up/down through the menu, right into a screen's buttons, arrows between
        buttons, left from the leftmost button back to the menu."""
        now = time.monotonic() if now is None else now
        shown = self.focus_zone is not None and now - self.focus_at < self.FOCUS_S
        self.focus_at = self.last_touch = now
        if not shown:                               # first move only shows where the focus is
            self.focus_zone, self.focus_action = "rail", None
            return
        if self.focus_zone == "rail":
            if direction in ("up", "down"):
                i = SCREENS.index(self.screen) + (1 if direction == "down" else -1)
                self.screen = SCREENS[i % len(SCREENS)]
                self.state.info_until, self.screen_until = 0.0, 0.0
            elif direction == "right":
                self.focus_zone, self.focus_action = "content", None   # first button chosen at render
            return
        hits = self._content_hits()
        current = next((r for r, a in hits if a == self.focus_action), None)
        if current is None:
            return
        target = self._neighbour(current, hits, direction)
        if target:
            self.focus_action = target
        elif direction == "left":
            self.focus_zone, self.focus_action = "rail", None

    @staticmethod
    def _neighbour(rect, hits, direction: str) -> str | None:
        cx, cy = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
        best, best_score = None, None
        for r, action in hits:
            if r == rect:
                continue
            dx, dy = (r[0] + r[2]) / 2 - cx, (r[1] + r[3]) / 2 - cy
            ahead = {"right": dx > 5, "left": dx < -5, "down": dy > 5, "up": dy < -5}[direction]
            if not ahead:
                continue
            along, across = (abs(dx), abs(dy)) if direction in ("left", "right") else (abs(dy), abs(dx))
            score = along + 2 * across
            if best_score is None or score < best_score:
                best, best_score = action, score
        return best

    def select(self, now: float | None = None) -> str | None:
        """Press: in the menu, step into the screen's buttons; on a button, press it (like a tap,
        so Restart / Shut down still need a second press)."""
        now = time.monotonic() if now is None else now
        shown = self.focus_zone is not None and now - self.focus_at < self.FOCUS_S
        self.focus_at = self.last_touch = now
        if not shown:
            self.focus_zone, self.focus_action = "rail", None
            return None
        if self.focus_zone == "rail":
            if self._content_hits():
                self.focus_zone, self.focus_action = "content", None
            return None
        rect = next((r for r, a in self._content_hits() if a == self.focus_action), None)
        if rect is None:
            return None
        return self.tap((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2, now)

    def back(self, now: float | None = None) -> None:
        """Long press: home, focus on the menu."""
        now = time.monotonic() if now is None else now
        self.screen, self.screen_until, self.state.info_until = "home", 0.0, 0.0
        self.focus_zone, self.focus_action, self.focus_at, self.last_touch = "rail", None, now, now

    def _draw_focus(self, d, now: float) -> None:
        if self.focus_zone is None or now - self.focus_at >= self.FOCUS_S:
            return
        if self.focus_zone == "content":
            hits = self._content_hits()
            if not hits:                            # this screen has no buttons: back to the menu
                self.focus_zone = "rail"
            elif self.focus_action not in {a for _, a in hits}:
                self.focus_action = min(hits, key=lambda h: (h[0][1] // 20, h[0][0]))[1]   # top-left first
        if self.focus_zone == "rail":
            rect = next((r for r, a in self._hits if a == f"nav:{self.screen}"), None)
            if rect:
                rect = (4, rect[1] + 1, RAIL - 8, rect[3] - 3)
        else:
            rect = next((r for r, a in self._hits if a == self.focus_action), None)
            if rect:
                rect = (rect[0] - 4, rect[1] - 4, rect[2] + 8, rect[3] + 8)
        if rect:
            d.rectangle(rect, outline=MAGENTA, width=4)

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
        elif name == "emotions":                                        # a smiley
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=k, width=3)
            d.rectangle((cx - 6, cy - 5, cx - 3, cy - 2), fill=k)
            d.rectangle((cx + 3, cy - 5, cx + 6, cy - 2), fill=k)
            d.arc((cx - 7, cy - 4, cx + 7, cy + 7), 20, 160, fill=k, width=3)
        elif name == "about":                                           # an "i" in a circle
            d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), outline=k, width=3)
            d.rectangle((cx - 2, cy - 7, cx + 1, cy - 4), fill=k)
            d.rectangle((cx - 2, cy - 1, cx + 1, cy + 7), fill=k)
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
            rect = (7, 4 + i * ROW, RAIL - 13, 4 + i * ROW + 36)
            active = self.screen == name
            fill = TEAL if active else WHITE
            x0, y0, x1, y1 = rect
            d.rectangle((x0 + 3, y0 + 3, x1 + 3, y1 + 3), fill=BLACK)
            d.rectangle(rect, fill=fill, outline=BLACK, width=3)
            self._icon(d, name, (x0 + x1) // 2, (y0 + y1) // 2)
            self._hits.append(((0, 2 + i * ROW, RAIL, 2 + (i + 1) * ROW - 1), f"nav:{name}"))

    def _top(self, d, expr: str, now: float) -> None:
        d.text((X0, 6), "AIRA", font=self.f_title, fill=BLACK, stroke_width=1, stroke_fill=BLACK)
        x = X0 + int(self.f_title.getlength("AIRA")) + 12
        if self.screen == "home" or self.screen == "chat":
            self.tag(d, x, 12, *self._state_label(expr))
        else:
            self.tag(d, x, 12, self.screen.upper(), YELLOW)
        d.text((X1, 10), time.strftime("%H:%M"), font=self.f_px, fill=BLACK, anchor="ra")
        tw = int(self.f_px.getlength("00:00"))
        d.rectangle((X1 - tw - 22, 15, X1 - tw - 10, 27), fill=TEAL if self.state.linked else YELLOW,
                    outline=BLACK, width=2)
        d.line((X0, TOP - 4, X1, TOP - 4), fill=BLACK, width=3)

    def _state_label(self, expr: str):
        name = self._sprite_name(expr)
        if self.state.expression == "idle" and name in MOOD_TAG:
            return MOOD_TAG[name], YELLOW if name != "happy" else TEAL
        return STATE_TAG.get(self.state.expression, ("READY", WHITE))

    def _fit(self, frame: Image.Image, size: tuple[int, int]) -> Image.Image:
        """Scale to cover ``size`` keeping the aspect ratio, then crop the middle (never squashes her)."""
        key = (id(frame), size)
        img = self._scaled.get(key)
        if img is None:
            w, h = size
            k = max(w / frame.width, h / frame.height)
            sw, sh = max(w, round(frame.width * k)), max(h, round(frame.height * k))
            img = frame.resize((sw, sh), Image.Resampling.LANCZOS)
            if (sw, sh) != (w, h):
                left, top = (sw - w) // 2, (sh - h) // 2
                img = img.crop((left, top, left + w, top + h))
            self._scaled[key] = img
        return img

    def _mascot(self, name: str, now: float, size=MASCOT) -> Image.Image:
        return self._fit(self._frame(name, now), size)

    def _card(self, img, d, x: int, y: int, picture: Image.Image) -> tuple[int, int, int, int]:
        """AIRA's framed picture with the hard 6 px shadow; returns the card rectangle."""
        w, h = picture.size
        rect = (x, y, x + w + 5, y + h + 5)
        d.rectangle((x + 6, y + 6, rect[2] + 6, rect[3] + 6), fill=BLACK)
        d.rectangle(rect, fill=NIGHT, outline=BLACK, width=3)
        img.paste(picture, (x + 3, y + 3))
        return rect

    def _link_label(self) -> tuple[str, tuple]:
        if self.state.linked:
            return "ONLINE", TEAL
        if self.state.online_ai:
            return "ONLINE AI", MAGENTA                          # answering on her own, without the laptop
        return "OFFLINE", YELLOW

    def _stats_row(self, d, y: int) -> None:
        st = self.stats.data
        x = X0
        for text in ([f"CPU {st['cpu']}%"] if st["cpu"] is not None else []) + \
                    ([f"RAM {st['ram_used']}/{st['ram_total']}"] if st["ram_used"] is not None else []) + \
                    ([f"{st['temp']}°C"] if st["temp"] is not None else []):
            x += self.tag(d, x, y, text, WHITE) + 6
        label, fill = self._link_label()
        self.tag(d, X1, y, label, fill, anchor_right=True)

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
        """Big AIRA on the left, a status column on the right, her words underneath like subtitles."""
        s = self.state
        if not (s.linked or s.online_ai):                      # nobody to answer: sleepy clock
            self._standby(img, d, now)
            return
        name = self._sprite_name(expr)
        x0, y0, x1, y1 = self._card(img, d, X0, 6, self._mascot(name, now))
        if name == "listening":
            for i in range(5):                                  # live level bars on her card
                h = 6 + 14 * abs(math.sin(now * 6 + i))
                d.rectangle((x0 + 12 + i * 9, y1 - 10 - h, x0 + 17 + i * 9, y1 - 10), fill=TEAL, outline=BLACK)
        # status column
        px = x1 + 14
        d.text((px, 2), "AIRA", font=self.f_title, fill=BLACK, stroke_width=1, stroke_fill=BLACK)
        self.tag(d, px, 36, *self._state_label(expr))
        d.rectangle((px, 69, px + 12, 81), fill=TEAL, outline=BLACK, width=2)
        d.text((px + 18, 64), time.strftime("%H:%M"), font=self.f_px, fill=BLACK)
        st = self.stats.data
        y = 96
        for text in ([f"CPU {st['cpu']}%"] if st["cpu"] is not None else []) + \
                    ([f"RAM {st['ram_used']}/{st['ram_total']}"] if st["ram_used"] is not None else []) + \
                    ([f"{st['temp']}°C"] if st["temp"] is not None else []):
            self.tag(d, px, y, text, WHITE)
            y += 26
        self.tag(d, px, y1 - 20, *self._link_label())
        # what she is saying
        if s.caption and now <= s.caption_until:
            text, role = s.caption, s.caption_role
        else:
            text = {"listening": "Listening...", "thinking": "Hmm, let me think...", "sleepy": "Taking a short break...",
                    "error": "Something went wrong. Let's check it."}.get(name, "Hey! What are we working on?")
            role = "aira"
        self._bubble(d, (X0, y1 + 11, X1 - 4, H - 6), text, role, now)

    def _standby(self, img, d, now: float) -> None:
        _, _, x1, y1 = self._card(img, d, X0, TOP + 2, self._mascot("sleepy", now, STANDBY))
        left = x1 + 12
        cx = (left + X1) // 2
        d.text((cx, TOP + 30), time.strftime("%H:%M"), font=self.f_huge, fill=BLACK, anchor="ma",
               stroke_width=1, stroke_fill=BLACK)
        d.text((cx, TOP + 104), time.strftime("%a, %d %b %Y").upper(), font=self.f_small, fill=MUTED, anchor="ma")
        msg = (self.state.status_text or "see you soon").upper()
        w = int(self.f_tag.getlength(msg)) + 12
        self.tag(d, cx - w // 2, TOP + 130, msg, YELLOW)
        if self.state.caption and now <= self.state.caption_until:
            for i, line in enumerate(self._wrap(self.state.caption, X1 - left)[:2]):
                self._text(d, (left, TOP + 160 + i * 18), line, BLACK)
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

    def _emotions_screen(self, img, d, expr: str, now: float) -> None:
        """Try AIRA's emotions: tap one, or AUTO to see them all in turn."""
        names = [e for e in EMOTIONS if e[0] in self.sprites]
        if self.emotion_auto and names:
            current = names[int((now - self.emotion_since) / EMOTION_S) % len(names)]
        else:
            current = next((e for e in names if e[0] == self.emotion), names[0] if names else ("idle", "IDLE", ""))
        name, label, about = current
        self._card(img, d, X0, TOP + 2, self._mascot(name, now, EMO_PREVIEW))
        px = X0 + EMO_PREVIEW[0] + 22
        d.text((px, TOP + 4), label, font=self.f_title, fill=BLACK, stroke_width=1, stroke_fill=BLACK)
        d.text((px, TOP + 36), about, font=self.f_small, fill=BLACK)
        d.text((px, TOP + 56), "Playing each in turn" if self.emotion_auto else "Tap one to try it",
               font=self.f_tag, fill=MUTED)
        self.button(d, (px, TOP + 96, X1 - 4, TOP + 140), "STOP" if self.emotion_auto else "AUTO PLAY",
                    "emo:auto", YELLOW if self.emotion_auto else WHITE, now)
        cols, gap, h = 5, 8, 42
        w = (X1 - X0 - 6 - gap * (cols - 1)) // cols
        for i, (key, short, _) in enumerate(names):
            r, c = divmod(i, cols)
            x0, y0 = X0 + c * (w + gap), TOP + 164 + r * (h + 8)
            self.button(d, (x0, y0, x0 + w, y0 + h), short, f"emo:{key}", TEAL if key == name else WHITE, now)

    def _about_screen(self, img, d, expr: str, now: float) -> None:
        """Who made AIRA, which version, and what this Pi is (read live, refreshed every 30 s)."""
        from .sysinfo import about_rows

        at, rows = self._about_rows
        if now - at > 30 or not rows:
            rows = about_rows(self.about_info.get("edition", "AIRA Half"), self.about_info.get("touch", ""))
            self._about_rows = (now, rows)
        self._card(img, d, X0, TOP + 2, self._mascot("happy", now, (80, 64)))
        d.text((X0 + 100, TOP + 6), "AIRA", font=self.f_title, fill=BLACK, stroke_width=1, stroke_fill=BLACK)
        d.text((X0 + 100, TOP + 38), "Autonomous Intelligent Responsive Assistant", font=self.f_small, fill=BLACK)
        y, label_w = TOP + 80, 66
        for label, value in rows[:11]:
            d.text((X0, y), label.upper(), font=self.f_tag, fill=MUTED)
            vmax = X1 - X0 - label_w - 4
            while value and self.f_small.getlength(value) > vmax:
                value = value[:-2].rstrip() + "…"
            d.text((X0 + label_w, y - 1), value, font=self.f_small, fill=BLACK)
            y += 17

    def _toast(self, d, text: str) -> None:
        w = max(220, int(self._measure(text)) + 70)
        x0 = X0 + (X1 - X0 - w) // 2
        y0 = 120
        self.box(d, (x0, y0, x0 + w, y0 + 52), YELLOW, shadow=6)
        d.rectangle((x0 + 12, y0 + 14, x0 + 36, y0 + 38), fill=BLACK)
        d.text((x0 + 24, y0 + 26), "i", font=self.f_px, fill=YELLOW, anchor="mm")
        self._text(d, (x0 + 48, y0 + 16), text, BLACK)

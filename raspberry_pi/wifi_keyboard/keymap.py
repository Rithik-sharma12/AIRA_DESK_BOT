"""Key names, Linux key codes and text -> keystroke mapping for the Wi-Fi keyboard.

Codes are the stable values from linux/input-event-codes.h, so this module needs no
third-party package. Only keys in ALLOWED are ever injected; SysRq is deliberately absent.

Characters are mapped for the console keyboard layout the Pi uses (/etc/default/keyboard,
XKBLAYOUT). Raspberry Pi OS with a GB locale normally uses "gb", where shift+2 is '"' and
'@' is shift+apostrophe; sending US keystrokes to a GB console gives the wrong symbols.
"""

from __future__ import annotations

from dataclasses import dataclass

KEY = {
    "KEY_ESC": 1, "KEY_1": 2, "KEY_2": 3, "KEY_3": 4, "KEY_4": 5, "KEY_5": 6, "KEY_6": 7,
    "KEY_7": 8, "KEY_8": 9, "KEY_9": 10, "KEY_0": 11, "KEY_MINUS": 12, "KEY_EQUAL": 13,
    "KEY_BACKSPACE": 14, "KEY_TAB": 15, "KEY_Q": 16, "KEY_W": 17, "KEY_E": 18, "KEY_R": 19,
    "KEY_T": 20, "KEY_Y": 21, "KEY_U": 22, "KEY_I": 23, "KEY_O": 24, "KEY_P": 25,
    "KEY_LEFTBRACE": 26, "KEY_RIGHTBRACE": 27, "KEY_ENTER": 28, "KEY_LEFTCTRL": 29,
    "KEY_A": 30, "KEY_S": 31, "KEY_D": 32, "KEY_F": 33, "KEY_G": 34, "KEY_H": 35, "KEY_J": 36,
    "KEY_K": 37, "KEY_L": 38, "KEY_SEMICOLON": 39, "KEY_APOSTROPHE": 40, "KEY_GRAVE": 41,
    "KEY_LEFTSHIFT": 42, "KEY_BACKSLASH": 43, "KEY_Z": 44, "KEY_X": 45, "KEY_C": 46,
    "KEY_V": 47, "KEY_B": 48, "KEY_N": 49, "KEY_M": 50, "KEY_COMMA": 51, "KEY_DOT": 52,
    "KEY_SLASH": 53, "KEY_RIGHTSHIFT": 54, "KEY_LEFTALT": 56, "KEY_SPACE": 57,
    "KEY_CAPSLOCK": 58, "KEY_F1": 59, "KEY_F2": 60, "KEY_F3": 61, "KEY_F4": 62, "KEY_F5": 63,
    "KEY_F6": 64, "KEY_F7": 65, "KEY_F8": 66, "KEY_F9": 67, "KEY_F10": 68,
    "KEY_102ND": 86, "KEY_F11": 87, "KEY_F12": 88, "KEY_RIGHTCTRL": 97, "KEY_RIGHTALT": 100,
    "KEY_HOME": 102, "KEY_UP": 103, "KEY_PAGEUP": 104, "KEY_LEFT": 105, "KEY_RIGHT": 106,
    "KEY_END": 107, "KEY_DOWN": 108, "KEY_PAGEDOWN": 109, "KEY_INSERT": 110,
    "KEY_DELETE": 111, "KEY_LEFTMETA": 125, "KEY_RIGHTMETA": 126, "KEY_COMPOSE": 127,
}
ALLOWED = frozenset(KEY)
MODIFIERS = frozenset({"KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
                       "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA"})


@dataclass(frozen=True)
class Stroke:
    key: str
    shift: bool = False


def _base() -> dict[str, Stroke]:
    m: dict[str, Stroke] = {}
    for c in "abcdefghijklmnopqrstuvwxyz":
        m[c] = Stroke(f"KEY_{c.upper()}")
        m[c.upper()] = Stroke(f"KEY_{c.upper()}", True)
    for d in "1234567890":
        m[d] = Stroke(f"KEY_{d}")
    m.update({
        " ": Stroke("KEY_SPACE"), "\n": Stroke("KEY_ENTER"), "\t": Stroke("KEY_TAB"),
        "-": Stroke("KEY_MINUS"), "_": Stroke("KEY_MINUS", True),
        "=": Stroke("KEY_EQUAL"), "+": Stroke("KEY_EQUAL", True),
        "[": Stroke("KEY_LEFTBRACE"), "{": Stroke("KEY_LEFTBRACE", True),
        "]": Stroke("KEY_RIGHTBRACE"), "}": Stroke("KEY_RIGHTBRACE", True),
        ";": Stroke("KEY_SEMICOLON"), ":": Stroke("KEY_SEMICOLON", True),
        ",": Stroke("KEY_COMMA"), "<": Stroke("KEY_COMMA", True),
        ".": Stroke("KEY_DOT"), ">": Stroke("KEY_DOT", True),
        "/": Stroke("KEY_SLASH"), "?": Stroke("KEY_SLASH", True),
        "!": Stroke("KEY_1", True), "$": Stroke("KEY_4", True), "%": Stroke("KEY_5", True),
        "^": Stroke("KEY_6", True), "&": Stroke("KEY_7", True), "*": Stroke("KEY_8", True),
        "(": Stroke("KEY_9", True), ")": Stroke("KEY_0", True),
        "'": Stroke("KEY_APOSTROPHE"),
    })
    return m


def _us() -> dict[str, Stroke]:
    m = _base()
    m.update({
        "@": Stroke("KEY_2", True), "#": Stroke("KEY_3", True), '"': Stroke("KEY_APOSTROPHE", True),
        "`": Stroke("KEY_GRAVE"), "~": Stroke("KEY_GRAVE", True),
        "\\": Stroke("KEY_BACKSLASH"), "|": Stroke("KEY_BACKSLASH", True),
    })
    return m


def _gb() -> dict[str, Stroke]:
    m = _base()
    m.update({
        '"': Stroke("KEY_2", True), "£": Stroke("KEY_3", True), "@": Stroke("KEY_APOSTROPHE", True),
        "#": Stroke("KEY_BACKSLASH"), "~": Stroke("KEY_BACKSLASH", True),  # key left of Enter
        "\\": Stroke("KEY_102ND"), "|": Stroke("KEY_102ND", True),          # key left of Z
        "`": Stroke("KEY_GRAVE"), "¬": Stroke("KEY_GRAVE", True),
    })
    return m


LAYOUTS = {"us": _us(), "gb": _gb()}


def text_to_strokes(text: str, layout: str = "gb") -> tuple[list[Stroke], str]:
    """Map text to keystrokes. Returns (strokes, characters that cannot be typed).

    Characters outside the layout (Tamil script, emoji, accented letters) cannot be typed
    on a Linux text console with a plain keyboard device and are reported back instead.
    """
    table = LAYOUTS[layout]
    strokes, skipped = [], []
    for ch in text.replace("\r\n", "\n").replace("\r", "\n"):
        s = table.get(ch)
        if s is None:
            skipped.append(ch)
        else:
            strokes.append(s)
    return strokes, "".join(skipped)

"""Instant commands: common requests that are handled without asking the AI model.

"volume up", "pause the music", "open chrome", "set a timer for 5 minutes",
"சத்தம் கூட்டு", "chrome open pannu" ... are unambiguous, so AIRA runs the tool
straight away and answers in well under a second. Anything that does not match one of
these patterns exactly goes to the model, which decides what to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .tools.apps import AppCatalog


@dataclass
class Route:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)


_POLITE = r"(?:please|pls|plz|aira|hey aira|can you|could you|kindly|just)"
_TANGLISH_DO = r"(?:pannu|panu|pannunga|pannungo|panna|pannidu|pannu da|di|da|ma|please)?"


def _clean(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[!?.,।]+", " ", text)
    text = re.sub(rf"^(?:{_POLITE}\s+)+", "", text)
    text = re.sub(rf"(?:\s+{_POLITE})+$", "", text)
    return re.sub(r"\s+", " ", text).strip()


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
              "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "sixty": 60,
              "a": 1, "an": 1, "half an": 0.5, "half a": 0.5}
_NUM = r"(\d+(?:\.\d+)?|half an|half a|forty five|one|two|three|four|five|six|seven|eight|nine|ten|fifteen|twenty|thirty|forty|sixty|an|a)"


def _num(s: str) -> float:
    return float(s) if re.fullmatch(r"\d+(?:\.\d+)?", s) else float(_NUM_WORDS[s])


class FastRouter:
    def __init__(self, catalog: AppCatalog):
        self.catalog = catalog
        self.rules: list[tuple[re.Pattern, Any]] = [
            # ---- volume
            (re.compile(r"^(?:turn |increase |raise )?(?:the )?(?:volume|sound) (?:up|increase|higher|louder)$"
                        rf"|^(?:increase|raise) (?:the )?(?:volume|sound)$|^louder$|^volume up$"
                        rf"|^(?:volume|sound)(?: ah| a)? (?:increase|koottu|kootu|jaasthi) {_TANGLISH_DO}$"
                        r"|^சத்தம் கூட்டு.*$|^சத்தத்தை கூட்டு.*$"), lambda m: Route("volume", {"action": "up"})),
            (re.compile(r"^(?:turn |decrease |lower )?(?:the )?(?:volume|sound) (?:down|decrease|lower|softer)$"
                        rf"|^(?:decrease|lower|reduce) (?:the )?(?:volume|sound)$|^quieter$|^volume down$"
                        rf"|^(?:volume|sound)(?: ah| a)? (?:decrease|korai|kurai|korachidu|kammi) {_TANGLISH_DO}$"
                        r"|^சத்தம் குறை.*$|^சத்தத்தை குறை.*$"), lambda m: Route("volume", {"action": "down"})),
            (re.compile(r"^(?:set |change )?(?:the )?volume (?:to |at )?(\d{1,3})(?: ?%| percent)?$"),
             lambda m: Route("volume", {"action": "set", "level": min(100, int(m.group(1)))})),
            (re.compile(rf"^(?:mute|unmute)(?: the)?(?: sound| volume| laptop)?$|^mute {_TANGLISH_DO}$"),
             lambda m: Route("volume", {"action": "mute"})),
            # ---- media
            (re.compile(r"^(?:pause|resume|play|stop|continue)(?: the)?(?: music| song| video| playback| it)?$"
                        rf"|^(?:music|song|paattu|pattu)(?: ah| a)? (?:pause|play|nirutthu|niruthu|stop) {_TANGLISH_DO}$"
                        r"|^(?:பாட்டு|இசை) (?:நிறுத்து|போடு).*$"),
             lambda m: Route("media_control", {"action": "play_pause"})),
            (re.compile(r"^(?:next|skip)(?: the)?(?: song| track| video)?$|^(?:play )?next (?:song|track)$"
                        rf"|^(?:next|aduttha|adutha) (?:song|paattu|pattu) {_TANGLISH_DO}$|^அடுத்த பாட்டு.*$"),
             lambda m: Route("media_control", {"action": "next"})),
            (re.compile(r"^(?:previous|last|go back)(?: song| track)?$|^(?:play )?previous (?:song|track)$"),
             lambda m: Route("media_control", {"action": "previous"})),
            # ---- timers
            (re.compile(rf"^(?:set |start |put )?(?:a |the )?timer (?:for |of )?{_NUM} (second|seconds|sec|secs|minute|minutes|min|mins|hour|hours|hr|hrs)$"
                        rf"|^{_NUM} (second|seconds|sec|secs|minute|minutes|min|mins|hour|hours) timer(?: {_TANGLISH_DO})?$"),
             self._timer),
            # ---- power (lock is safe; the rest are confirmed by the engine)
            (re.compile(r"^lock(?: the| my)?(?: laptop| computer| screen| pc| system)?$|^(?:laptop|screen) lock " + _TANGLISH_DO + "$"),
             lambda m: Route("lock_screen")),
            (re.compile(r"^cancel(?: the)? (?:shutdown|restart)$|^(?:stop|abort)(?: the)? (?:shutdown|restart)$"),
             lambda m: Route("cancel_shutdown")),
            (re.compile(r"^(?:shut ?down|shutdown|power off|turn off)(?: the| my)?(?: laptop| computer| pc| system)?$"),
             lambda m: Route("power", {"action": "shutdown"})),
            (re.compile(r"^(?:restart|reboot)(?: the| my)?(?: laptop| computer| pc| system)?$"),
             lambda m: Route("power", {"action": "restart"})),
            # ---- time & status
            (re.compile(r"^what(?:'s| is) the time(?: now)?$|^what time is it(?: now)?$|^time (?:enna|ennachu|ena)$"
                        r"|^(?:ippo )?mani (?:enna|ena|evvalavu)$|^மணி என்ன.*$|^நேரம் என்ன.*$"),
             lambda m: Route("get_datetime")),
            (re.compile(r"^(?:what(?:'s| is) (?:the |my )?)?battery(?: level| percentage| status)?$"
                        r"|^how much battery(?: is left| left| do i have)?$|^battery (?:evvalavu|enna|ethana)(?: iruku| irukku)?$"),
             lambda m: Route("system_status", {"what": "battery"})),
        ]

    def _timer(self, m: re.Match) -> Route | None:
        groups = [g for g in m.groups() if g]
        if len(groups) < 2:
            return None
        amount, unit = _num(groups[0]), groups[1]
        key = "seconds" if unit.startswith("s") else "hours" if unit.startswith("h") else "minutes"
        return Route("set_timer", {key: amount})

    def match(self, text: str) -> Route | None:
        if len(text) > 80:  # long utterances are conversations, not commands
            return None
        clean = _clean(text)
        if not clean:
            return None
        for pattern, build in self.rules:
            m = pattern.match(clean)
            if m:
                return build(m)
        return self._open_close(clean)

    _OPEN = re.compile(r"^(?:open|launch|start|run)\s+(.+?)$|^(.+?)\s+(?:open|launch|start)(?:\s+(?:pannu|panu|pannunga|pannidu|da|di|please))?$"
                       r"|^(.+?)(?:-ஐ|ஐ)?\s*(?:திற|திறந்து விடு|ஓபன் பண்ணு).*$")
    _CLOSE = re.compile(r"^(?:close|quit|exit|kill)\s+(.+?)$|^(.+?)\s+(?:close)(?:\s+(?:pannu|panu|pannunga|da|di|please))?$")

    def _open_close(self, clean: str) -> Route | None:
        for regex, tool in ((self._CLOSE, "close_app"), (self._OPEN, "open_app")):
            m = regex.match(clean)
            if not m:
                continue
            target = next(g for g in m.groups() if g).strip()
            app = self.catalog.find(target)
            if app:
                return Route(tool, {"name": app.key})
            if tool == "open_app":
                site = self.catalog.sites.get(re.sub(r"\s+", " ", target)) or self.catalog.sites.get(target.replace(" ", ""))
                if site:
                    return Route("open_website", {"site": target})
        return None


# ------------------------------------------------------------- confirmations
_YES = re.compile(r"^(?:yes|yeah|yep|yup|ya|ok|okay|sure|confirm|confirmed|do it|go ahead|go on|proceed|correct|"
                  r"sari|seri|sariya|aama|aamaa|ama|aam|haan|ha|pannu|pannunga|ஆமா|ஆம்|சரி|பண்ணு|செய்)(?=\s|$)", re.I)
_NO = re.compile(r"^(?:no|nope|nah|cancel|stop|don'?t|do not|never ?mind|wait|vendam|vendaam|venam|venaam|illa|illai|"
                 r"vaanam|வேண்டாம்|வேணாம்|இல்லை|இல்ல|நிறுத்து)(?=\s|$)", re.I)


def confirmation_answer(text: str) -> bool | None:
    """True for yes, False for no, None if the reply is neither (treated as a new request)."""
    clean = _clean(text)
    if _NO.match(clean):
        return False
    if _YES.match(clean):
        return True
    return None


def detect_lang(text: str) -> str:
    return "ta" if re.search(r"[஀-௿]", text) else "en"

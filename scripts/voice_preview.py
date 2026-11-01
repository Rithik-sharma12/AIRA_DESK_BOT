"""Hear AIRA's voice styles side by side before choosing one.

  python scripts\\voice_preview.py                 # default and sweet-girl styles
  python scripts\\voice_preview.py --pitch 12 18 24 # try several pitch raises for the girl style

--pitch is in percent. For Azure it sets the Tamil voice's pitch; for the offline Piper
voices it is converted to semitones (12% = 2, 18% = 2.9, 24% = 3.7). The output says
which engine spoke each variant.

Writes WAV files to voice_preview\\ (open them with any player). Uses the engines set
up in .env (Azure and/or offline Piper); the browser voice can only be tried in the page.
Pick a style with AIRA_TTS_VOICE_STYLE=girl (or default) in .env and restart the server.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from laptop_server.config import Settings  # noqa: E402
from laptop_server.voice.service import VoiceService, VoiceUnavailable  # noqa: E402

SAMPLES = {
    "tamil": "வணக்கம்! நான் AIRA. இன்னைக்கு என்ன பண்ணலாம்?",
    "tanglish": "சரி, உங்க Python error-ஐ பார்க்கலாம்!",
    "english": "Hi! I'm AIRA. Your timer is done, time for a little break!",
}


async def render(settings: Settings, tag: str, out: Path) -> tuple[list[str], set[str]]:
    voice = VoiceService(settings)
    if voice.tts_name is None:
        raise VoiceUnavailable("no server voice is set up (Azure key or Piper voices, HOW-TO-RUN section 3b)")
    files, engines = [], set()
    try:
        for name, text in SAMPLES.items():
            clip = await voice.speak(text)
            engines.add(getattr(voice._tts, "last_engine", None) or voice.tts_name)
            path = out / f"{tag}-{name}.wav"
            clip.save(path)
            files.append(path.name)
    finally:
        await voice.aclose()
    return files, engines


def semitones(pct: float) -> float:
    return round(12 * math.log2(1 + pct / 100), 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pitch", type=float, nargs="*", help="Tamil pitch raises (%%) to try for the girl style")
    ap.add_argument("--out", type=Path, default=ROOT / "voice_preview")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    base = Settings()
    variants = [("default", {"tts_voice_style": "default"}), ("girl", {"tts_voice_style": "girl"})]
    for p in a.pitch or []:
        variants.append((f"girl-pitch{p:+.0f}", {"tts_voice_style": "girl", "tts_girl_ta_pitch_pct": p,
                                                 "tts_girl_piper_semitones": semitones(p)}))
    try:
        for tag, changes in variants:
            s = base.model_copy(update=changes)
            files, engines = asyncio.run(render(s, tag, a.out))
            note = " + ".join(sorted(e.replace("piper", "piper (offline)") for e in engines))
            print(f"{tag:<16} [{note}] {', '.join(files)}")
        if a.pitch and "azure" not in VoiceService(base).tts_name:
            print("\nOffline voices: to use a pitch you like, put its semitones in .env, e.g. "
                  f"AIRA_TTS_GIRL_PIPER_SEMITONES={semitones(a.pitch[len(a.pitch) // 2])}")
    except VoiceUnavailable as exc:
        print(f"Cannot preview: {exc}")
        print("Meanwhile, the sweet-girl style already works with the browser voice: set "
              "AIRA_TTS_VOICE_STYLE=girl in .env, restart, and talk to AIRA in Edge (it has a child voice).")
        return 1
    print(f"\nListen in {a.out}. Choose with AIRA_TTS_VOICE_STYLE=girl|default in .env; "
          "fine-tune AIRA_TTS_GIRL_TA_PITCH_PCT, AIRA_TTS_GIRL_RATE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

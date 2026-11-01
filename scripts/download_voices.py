"""Download AIRA's offline Piper voices (used when Azure is not set up or offline).

  python scripts\\download_voices.py                # Tamil female + English female (~130 MB)
  python scripts\\download_voices.py --tamil male

Files go to voices\\ (git-ignored); AIRA finds them automatically at start-up.

Voices:
- Tamil:   tinisoft/piper-ta_IN-rasa_{female,male}-medium, trained on the AI4Bharat Rasa
           dataset. Licence CC-BY-4.0: credit "AI4Bharat Rasa" if you publish recordings.
- English: rhasspy/piper-voices en_US-lessac-medium (clear, neutral female voice).
Piper itself: pip install piper-tts  (already in requirements-voice.txt).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
HF = "https://huggingface.co"

VOICES = {
    "ta-female": f"{HF}/tinisoft/piper-ta_IN-rasa_female-medium/resolve/main/ta_IN-rasa_female-medium",
    "ta-male": f"{HF}/tinisoft/piper-ta_IN-rasa_male-medium/resolve/main/ta_IN-rasa_male-medium",
    "en-lessac": f"{HF}/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium",
    "en-amy": f"{HF}/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium",
}


def download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 1000:
        print(f"  already have {dest.name}")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done * 100 // total:3d}% of {total / 1e6:.0f} MB", end="", flush=True)
    print(f"\r  {dest.name}: done ({done / 1e6:.1f} MB)            ")
    tmp.replace(dest)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tamil", choices=["female", "male"], default="female")
    ap.add_argument("--english", choices=["lessac", "amy"], default="lessac")
    ap.add_argument("--out", type=Path, default=ROOT / "voices")
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for key in (f"ta-{a.tamil}", f"en-{a.english}"):
        base = VOICES[key]
        name = base.rsplit("/", 1)[1]
        print(f"{key}:")
        try:
            download(base + ".onnx.json", a.out / f"{name}.onnx.json")
            download(base + ".onnx", a.out / f"{name}.onnx")
        except httpx.HTTPError as exc:
            print(f"\n  FAILED: {exc}\n  Check your internet connection, or download the two files by hand from:\n"
                  f"  {base.rsplit('/resolve/', 1)[0]}")
            return 1
    try:
        import piper  # noqa: F401
    except ImportError:
        print("\nNow install the Piper engine:  pip install piper-tts")
    print(f"\nVoices are in {a.out}. Restart AIRA; the server log will say 'Piper ta voice loaded'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

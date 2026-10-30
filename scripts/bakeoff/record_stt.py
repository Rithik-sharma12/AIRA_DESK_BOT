"""Record the 50-utterance STT test set in your own voice, through AIRA's real microphone.

  python scripts/bakeoff/record_stt.py --list-devices
  python scripts/bakeoff/record_stt.py --device "speakerphone"     # name substring or index
  python scripts/bakeoff/record_stt.py --only-missing              # resume later

For each prompt: press Enter to start, read the line naturally (as you would talk to
AIRA, not slowly), press Enter to stop. Then Enter = keep, r = redo, s = skip, q = quit.
Recordings: bakeoff_recordings/<id>.wav (16 kHz mono 16-bit). They stay on your laptop
(the folder is git-ignored).
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import wave

from common import RECORDINGS, load_stt_prompts

RATE = 16000


def pick_device(sd, wanted: str | None):
    if wanted is None:
        return None
    if wanted.isdigit():
        return int(wanted)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and wanted.lower() in d["name"].lower():
            return i
    raise SystemExit(f"No input device matching '{wanted}'. Use --list-devices.")


def record_once(sd, device) -> bytes:
    q: queue.Queue = queue.Queue()

    def cb(indata, frames, t, status):  # runs in the audio thread
        if status:
            print(f"  ({status})", file=sys.stderr)
        q.put(bytes(indata))

    input("  [Enter] to start recording ")
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", device=device, callback=cb):
        input("  recording... [Enter] to stop ")
    chunks = []
    while not q.empty():
        chunks.append(q.get())
    return b"".join(chunks)


def save(path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--device", help="input device index or name substring")
    ap.add_argument("--only-missing", action="store_true")
    a = ap.parse_args()
    try:
        import sounddevice as sd
    except ImportError:
        print("Install recording support:  pip install sounddevice")
        return 2
    if a.list_devices:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print(f"{i:>3}  {d['name']}")
        return 0
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    device = pick_device(sd, a.device)
    prompts = load_stt_prompts()
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    (RECORDINGS / "prompts.json").write_text(json.dumps(prompts, indent=2, ensure_ascii=False), encoding="utf-8")
    done = 0
    for n, p in enumerate(prompts, 1):
        path = RECORDINGS / f"{p['id']}.wav"
        if a.only_missing and path.exists():
            continue
        print(f"\n[{n}/{len(prompts)}] {p['id']} ({p['category']})\n  >>  {p['text']}")
        while True:
            pcm = record_once(sd, device)
            secs = len(pcm) / 2 / RATE
            choice = input(f"  {secs:.1f}s captured. [Enter] keep, r redo, s skip, q quit: ").strip().lower()
            if choice == "r":
                continue
            if choice == "q":
                print(f"\nSaved {done} recordings in {RECORDINGS}")
                return 0
            if choice != "s":
                if secs < 0.5:
                    print("  too short, recording again")
                    continue
                save(path, pcm)
                done += 1
            break
    print(f"\nSaved {done} recordings in {RECORDINGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

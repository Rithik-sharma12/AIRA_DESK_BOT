"""Cut AIRA's starter mascot sprites out of the concept board (one still per emotion).

  python scripts\\make_mascot_sprites.py docs\\images\\aira_ui_concept.png

Writes raspberry_pi/aira_pi/assets/mascot/<emotion>/frame_00.png (300 x 240) and
manifest.json. These are placeholders cut from a mockup: replace them with real
animation frames when you have them (see the README in that folder). The crop boxes
match the 1536 x 1024 concept board; for another board, adjust BOXES.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "raspberry_pi" / "aira_pi" / "assets" / "mascot"
SIZE = (300, 240)

# (left, top, right, bottom) of the mascot area only (no title bar, no speech bubble)
BOXES = {
    "idle": (170, 84, 532, 374),
    "happy": (841, 54, 1066, 227),
    "thinking": (1205, 54, 1430, 227),
    "listening": (843, 306, 1066, 480),
    "speaking": (1207, 306, 1418, 480),
    "excited": (19, 582, 255, 746),
    "confused": (407, 582, 643, 746),
    "sleepy": (794, 582, 1030, 746),
    "error": (1173, 582, 1409, 746),
}
FPS = {"idle": 1, "happy": 2, "thinking": 2, "listening": 4, "speaking": 6, "excited": 4, "confused": 2,
       "sleepy": 1, "error": 2}


def cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Scale to fill ``size`` and centre-crop (no stretching)."""
    w, h = img.size
    scale = max(size[0] / w, size[1] / h)
    img = img.resize((round(w * scale), round(h * scale)), Image.Resampling.LANCZOS)
    left = (img.width - size[0]) // 2
    top = (img.height - size[1]) // 2
    return img.crop((left, top, left + size[0], top + size[1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", type=Path)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    board = Image.open(a.board).convert("RGB")
    if board.size != (1536, 1024):
        print(f"Note: board is {board.size}, crop boxes were made for 1536 x 1024; scaling them.")
    sx, sy = board.width / 1536, board.height / 1024
    manifest = {"size": list(SIZE), "source": "concept board (placeholder stills)", "emotions": {}}
    for name, (l, t, r, b) in BOXES.items():
        crop = board.crop((round(l * sx), round(t * sy), round(r * sx), round(b * sy)))
        sprite = cover(crop, SIZE)
        d = a.out / name
        d.mkdir(parents=True, exist_ok=True)
        sprite.save(d / "frame_00.png", optimize=True)
        manifest["emotions"][name] = {"fps": FPS[name], "frames": ["frame_00.png"]}
        print(f"  {name:<10} {crop.size[0]}x{crop.size[1]} -> {SIZE[0]}x{SIZE[1]}")
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"Wrote {len(BOXES)} sprites and manifest.json to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Turn AIRA's still sprites into animation frames: blinks and a talking mouth.

  python scripts\\make_mascot_animation.py

For each emotion with open eyes it paints a half-closed and a closed eyelid (skin blended
row by row from the face around the eye, then a lash line along the lid), and for
"speaking" it makes half-open and closed mouth shapes. Only the eye and mouth pixels
change, so on the Pi's SPI screen only those few rows are re-sent: smooth and cheap.

Run after scripts/make_mascot_sprites.py. Eye and mouth positions are measured for the
starter sprites (EYES / MOUTH below); re-measure them if you replace those sprites, or
hand-draw your own frames instead (see raspberry_pi/aira_pi/assets/mascot/README.md).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "raspberry_pi" / "aira_pi" / "assets" / "mascot"

# centre (x, y), half-width, half-height, tilt (degrees; negative = right end higher), outer corner side
EYES = {
    "idle":     [((146, 140), 21, 12, -14, "left"), ((212, 114), 18, 13, -12, "right")],
    "thinking": [((156, 133), 21, 14, -12, "left"), ((219, 103), 13, 14, -12, "right")],
    "speaking": [((219, 105), 15, 16, -12, "right")],
    "excited":  [((169, 120), 23, 19, -8, "left"), ((238, 95), 17, 17, -10, "right")],
    "confused": [((162, 124), 20, 17, -10, "left"), ((224, 96), 14, 16, -10, "right")],
    "error":    [((160, 123), 21, 16, -10, "left"), ((228, 102), 15, 14, -10, "right")],
}
# speaking: open mouth (centre, half-width, half-height, tilt)
MOUTH = {"speaking": ((199, 166), 23, 15, -14)}

LASH = (52, 26, 30)
LIP = (168, 70, 82)


def skin_mask(arr):
    """Skin-coloured pixels (excludes the orange-brown hair highlights)."""
    import numpy as np

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    rr = np.maximum(r, 1)
    return (r > 185) & (g / rr > 0.58) & (g / rr < 0.86) & (b / rr > 0.42) & (b / rr < 0.78)


class Region:
    """A tilted ellipse with separate top/bottom radii, in sprite pixels."""

    def __init__(self, center, a, b_top, b_bottom, tilt):
        self.cx, self.cy = center
        self.a, self.bt, self.bb = a, b_top, b_bottom
        self.th = math.radians(tilt)

    def local(self, x, y):
        dx, dy = x - self.cx, y - self.cy
        c, s = math.cos(self.th), math.sin(self.th)
        return dx * c + dy * s, -dx * s + dy * c

    def world(self, u, v):
        c, s = math.cos(self.th), math.sin(self.th)
        return self.cx + u * c - v * s, self.cy + u * s + v * c

    def contains(self, x, y, v_max: float | None = None) -> bool:
        u, v = self.local(x, y)
        b = self.bt if v < 0 else self.bb
        if v_max is not None and v > v_max:
            return False
        return (u / self.a) ** 2 + (v / b) ** 2 <= 1.0

    def bbox(self):
        r = int(max(self.a, self.bt, self.bb)) + 3
        return self.cx - r, self.cy - r, self.cx + r, self.cy + r


def inpaint(img: Image.Image, region: Region, v_max: float | None = None) -> None:
    """Replace the region with skin that diffuses in smoothly from the face around it.

    Pixels inside the region, plus dark lash pixels hugging it, are unknown; everything else
    is fixed. Repeated neighbour-averaging (a Laplace fill) grows the surrounding skin tones
    and shading into the hole without seams or stripes.
    """
    import numpy as np

    arr = np.asarray(img, dtype=np.float64).copy()
    h, w = arr.shape[:2]
    x0, y0, x1, y1 = region.bbox()
    x0, y0, x1, y1 = max(1, x0 - 6), max(1, y0 - 6), min(w - 2, x1 + 6), min(h - 2, y1 + 6)
    sub = arr[y0:y1 + 1, x0:x1 + 1]
    hole = np.zeros(sub.shape[:2], dtype=bool)
    for yy in range(sub.shape[0]):
        for xx in range(sub.shape[1]):
            hole[yy, xx] = region.contains(x0 + xx, y0 + yy, v_max)
    # dark lash pixels right next to the hole belong to the eye too
    near = hole.copy()
    for _ in range(3):
        near = near | np.roll(near, 1, 0) | np.roll(near, -1, 0) | np.roll(near, 1, 1) | np.roll(near, -1, 1)
    lum = sub[..., :3].mean(axis=2)
    if v_max is None:
        sat = sub[..., :3].max(axis=2) - sub[..., :3].min(axis=2)
        hole |= near & (lum < 115) & ~skin_mask(sub)            # lashes
        hole |= near & (lum > 185) & (sat < 60)                 # leftover eye white
    skin = skin_mask(sub) & ~hole
    fill = sub[skin].mean(axis=0) if skin.any() else np.array([238.0, 170.0, 135.0])
    sub[hole] = fill
    # Only skin (and already-filled) neighbours count, so dark hair or lip lines at the edge
    # of the hole cannot bleed in as shadows.
    valid = (skin | hole).astype(np.float64)[..., None]
    for _ in range(300):
        num = sum(np.roll(sub * valid, s, ax) for s, ax in ((1, 0), (-1, 0), (1, 1), (-1, 1)))
        den = sum(np.roll(valid, s, ax) for s, ax in ((1, 0), (-1, 0), (1, 1), (-1, 1)))
        avg = num / np.maximum(den, 1e-6)
        sub[hole] = avg[hole]
    arr[y0:y1 + 1, x0:x1 + 1] = sub
    img.paste(Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)))


def lash_line(img, region: Region, sag: float, v0: float, outer: str, width: int = 3, ticks: bool = True):
    """A closed lid: a gently sagging curve across the eye, with two lashes at the outer corner."""
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(25):
        t = -1 + 2 * i / 24
        pts.append(region.world(t * region.a * 0.95, v0 + sag * (1 - t * t)))
    d.line(pts, fill=LASH, width=width, joint="curve")
    if ticks:
        end = pts[0] if outer == "left" else pts[-1]
        sign = -1 if outer == "left" else 1
        for k, (du, dv) in enumerate(((sign * 5, 3), (sign * 3, 5))):
            ux, uy = region.world(0, 0)
            ex, ey = region.world(du, dv)
            d.line((end[0], end[1], end[0] + (ex - ux), end[1] + (ey - uy)), fill=LASH, width=2)


def blink_frames(img: Image.Image, eyes) -> tuple[Image.Image, Image.Image]:
    """Closed: lids painted over the whole eye. Half: the closed lid shown only above the
    lid line, so the lower half of the iris still peeks out."""
    import numpy as np

    closed = img.copy()
    for center, a, b, tilt, outer in eyes:
        inpaint(closed, Region(center, a + 3, b + 5, b + 3, tilt))   # also covers the upper lash line
    half_arr = np.asarray(img).copy()
    closed_arr = np.asarray(closed)
    for center, a, b, tilt, outer in eyes:
        lid = Region(center, a + 3, b + 5, b + 3, tilt)
        x0, y0, x1, y1 = lid.bbox()
        for y in range(max(0, y0), min(img.height, y1 + 1)):
            for x in range(max(0, x0), min(img.width, x1 + 1)):
                if lid.contains(x, y, v_max=-b * 0.02):
                    half_arr[y, x] = closed_arr[y, x]
    half = Image.fromarray(half_arr)
    for center, a, b, tilt, outer in eyes:
        lash_line(closed, Region(center, a + 1, b, b, tilt), sag=b * 0.35, v0=b * 0.15, outer=outer)
        lash_line(half, Region(center, a + 1, b, b, tilt), sag=b * 0.10, v0=-b * 0.06, outer=outer, ticks=False)
    return half, closed


def mouth_frames(img: Image.Image, mouth) -> tuple[Image.Image, Image.Image]:
    """Half-open: only the mouth's own pixels (lips, teeth, tongue) squashed to ~55% height
    over a skin-filled mouth area. Closed: skin-filled mouth plus a small smile line."""
    import numpy as np

    center, a, b, tilt = mouth
    region = Region(center, a + 7, b + 5, b + 5, tilt)
    x0, y0, x1, y1 = region.bbox()
    crop = img.crop((x0, y0, x1 + 1, y1 + 1))
    arr = np.asarray(crop)
    inside = np.array([[region.contains(x0 + x, y0 + y) for x in range(crop.width)] for y in range(crop.height)])
    mouth_px = inside & ~skin_mask(arr.astype(np.float64))
    mask = Image.fromarray((mouth_px * 255).astype(np.uint8))
    ys = np.where(mouth_px.any(axis=1))[0]
    top = int(ys.min()) if len(ys) else 0
    new_h = max(4, int(crop.height * 0.55))

    half = img.copy()
    inpaint(half, region)
    half.paste(crop.resize((crop.width, new_h), Image.Resampling.LANCZOS),
               (x0, y0 + top - int(top * 0.55)), mask.resize((crop.width, new_h), Image.Resampling.NEAREST))
    closed = img.copy()
    inpaint(closed, region)
    d = ImageDraw.Draw(closed)
    pts = [region.world(t * a * 0.62, -b * 0.25 + b * 0.30 * (1 - t * t)) for t in [-1 + 2 * i / 20 for i in range(21)]]
    d.line(pts, fill=LIP, width=2, joint="curve")
    return half, closed


def main() -> int:
    manifest_path = ASSETS / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, eyes in EYES.items():
        folder = ASSETS / name
        base = Image.open(folder / "frame_00.png").convert("RGB")
        half, closed = blink_frames(base, eyes)
        half.save(folder / "blink_01.png", optimize=True)
        closed.save(folder / "blink_02.png", optimize=True)
        manifest["emotions"][name]["blink"] = ["blink_01.png", "blink_02.png", "blink_02.png", "blink_01.png"]
        print(f"  {name:<10} blink frames")
    for name, mouth in MOUTH.items():
        folder = ASSETS / name
        base = Image.open(folder / "frame_00.png").convert("RGB")
        half, closed = mouth_frames(base, mouth)
        half.save(folder / "frame_01.png", optimize=True)
        closed.save(folder / "frame_02.png", optimize=True)
        manifest["emotions"][name].update(fps=7, frames=["frame_00.png", "frame_01.png", "frame_02.png",
                                                         "frame_01.png", "frame_00.png", "frame_02.png"])
        print(f"  {name:<10} mouth frames")
    manifest["source"] = "concept board stills + generated blink/mouth frames (scripts/make_mascot_animation.py)"
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

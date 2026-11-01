# AIRA mascot sprites

The Pi draws AIRA's screen from these sprites plus live elements (title bar, speech bubble,
Pi stats, clock, notifications), so text and numbers are always real and crisp.

What is here now:
- one still per emotion, cut from the concept board by `scripts/make_mascot_sprites.py`;
- **generated animation** from `scripts/make_mascot_animation.py`:
  - blink frames (half-closed, closed) for every emotion with open eyes. AIRA blinks every
    2.5 to 6 s, sometimes twice;
  - three mouth shapes (open, half, closed) that cycle while she speaks.

Rebuild everything with:
```powershell
python scripts\make_mascot_sprites.py docs\images\aira_ui_concept.png
python scripts\make_mascot_animation.py
```
Replace any of it with hand-made frames whenever you like; no code changes needed.

## Spec

| Item | Value |
|---|---|
| Frame size | **300 × 240 px** (other sizes are scaled to fill and centre-cropped) |
| Format | **Either** full scenes (PNG RGB, background included) **or** a transparent character (PNG with alpha, any size such as 160 × 160) that AIRA stands on the bottom edge of `background.png` (300 × 240; a plain night colour if missing) |
| Style | Pixel art; keep the same character (long dark wavy hair, lavender flower on the left, bindi, drop earrings, purple floral top) and the night-room purple background |
| Palette tip | The screen is RGB565 (65k colours): avoid long smooth gradients, they band. Flat pixel-art colours look best |
| Folder | `<emotion>/frame_00.png`, `frame_01.png`, ... |
| Emotions | `idle`, `happy`, `thinking`, `listening`, `speaking`, `excited`, `confused`, `sleepy`, `error` (missing ones fall back to `idle`) |

`manifest.json` lists frames and speed per emotion:
```json
{"size": [300, 240], "emotions": {
  "idle":     {"fps": 2, "frames": ["frame_00.png", "frame_01.png", "frame_00.png", "frame_00.png"]},
  "speaking": {"fps": 6, "frames": ["frame_00.png", "frame_01.png", "frame_02.png"]}
}}
```
Repeat a frame name to hold it longer; repeated names are loaded only once.

`"blink"` (optional) lists frames played over the loop now and then, at 14 fps:
```json
"idle": {"fps": 1, "frames": ["frame_00.png"], "blink": ["blink_01.png", "blink_02.png", "blink_02.png", "blink_01.png"]}
```

## Suggested frames (small set, big effect)

| Emotion | Frames | Notes |
|---|---|---|
| idle | 1-4: gentle breathing / hair sway | blinks go in `"blink"`, not in the loop |
| speaking | 3: mouth closed, half open, open | fps 6 |
| listening | 2: eyes closed, head tilted slightly | fps 2 |
| thinking | 2: eyes up-left, eyes up-right | fps 1 |
| happy, excited, confused, sleepy, error | 1-2 each | small changes only (sparkle, breathing) |

**Keep frames identical except for what moves.** The 3.5" screen is driven over SPI and
the Pi only re-sends the rows that changed, so a blink (a few rows) is smooth; a frame
where the whole picture shifts by a pixel forces a full-screen update (slower, flickery).

About size: 160 × 160 transparent frames work, but on this 480 × 320 screen the 300 × 240
scene shows AIRA's face about twice as large, so 300 × 240 (or a character about 240 px
tall) looks better. Pixelorama (free) is a good editor for frames: onion skinning helps
keep everything except the eyes or mouth identical.

Generating frames with an image tool works best when you start from one approved
`idle` frame and ask for the *same image* with only the eyes or mouth changed, on the
same background, at 300 × 240 (or 600 × 480; it is scaled down).

## Try them without a Pi
```bash
cd raspberry_pi
python3 -m aira_pi demo --out /tmp/faces --theme mascot
```
Use `"theme": "simple"` in `~/.config/aira-pi/config.json` to go back to the vector eyes.

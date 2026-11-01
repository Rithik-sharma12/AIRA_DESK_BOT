"""Drawing frames on the 3.5" ILI9486 TFT through its Linux framebuffer (/dev/fb1).

The SPI link to the screen is slow (a full 480x320 frame takes roughly 0.15 s), so only
the rows that changed since the last frame are written. A blinking eye or a talking
mouth touches a small band of rows, which keeps animation smooth on the Zero 2 W.

``PreviewDisplay`` writes PNG files instead, for trying the face on a PC.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("aira_pi.display")
SYS = Path("/sys/class/graphics")


def find_framebuffer(preferred: str | None = None) -> str | None:
    """The ILI9486/fbtft framebuffer, else the given/last one. None if there is none."""
    if preferred:
        return preferred if Path(preferred).exists() else None
    candidates = sorted(SYS.glob("fb[0-9]*")) if SYS.exists() else []
    for fb in candidates:
        try:
            name = (fb / "name").read_text().strip().lower()
        except OSError:
            continue
        if "ili9486" in name or name.startswith("fb_") or "fbtft" in name:
            return f"/dev/{fb.name}"
    return "/dev/fb1" if Path("/dev/fb1").exists() else None


def _read(fb: str, attr: str) -> str:
    return (SYS / Path(fb).name / attr).read_text().strip()


def to_rgb565(img) -> bytes:
    """PIL RGB image -> little-endian RGB565 bytes (the ILI9486 framebuffer format)."""
    import numpy as np

    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
    return v.astype("<u2").tobytes()


def to_bgra(img) -> bytes:
    import numpy as np

    a = np.asarray(img.convert("RGB"), dtype=np.uint8)
    out = np.empty(a.shape[:2] + (4,), dtype=np.uint8)
    out[..., 0], out[..., 1], out[..., 2], out[..., 3] = a[..., 2], a[..., 1], a[..., 0], 255
    return out.tobytes()


class Framebuffer:
    def __init__(self, path: str, rotate: int = 0):
        self.path = path
        self.rotate = rotate % 360
        self.width, self.height = (int(x) for x in _read(path, "virtual_size").split(","))
        self.bpp = int(_read(path, "bits_per_pixel"))
        try:
            self.stride = int(_read(path, "stride"))
        except (OSError, ValueError):
            self.stride = self.width * self.bpp // 8
        if self.bpp not in (16, 32):
            raise RuntimeError(f"{path}: {self.bpp} bits per pixel is not supported")
        self._fd = os.open(path, os.O_RDWR)
        self._last: bytes | None = None
        log.info("Display %s: %dx%d, %d bpp", path, self.width, self.height, self.bpp)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def show(self, img) -> int:
        """Write the image; returns how many rows were sent to the screen."""
        if self.rotate:
            img = img.rotate(self.rotate)
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height))
        data = to_rgb565(img) if self.bpp == 16 else to_bgra(img)
        row = self.width * self.bpp // 8
        first, last = 0, self.height - 1
        if self._last is not None and len(self._last) == len(data):
            mv_new, mv_old = memoryview(data), memoryview(self._last)
            while first <= last and mv_new[first * row:(first + 1) * row] == mv_old[first * row:(first + 1) * row]:
                first += 1
            if first > last:
                return 0
            while mv_new[last * row:(last + 1) * row] == mv_old[last * row:(last + 1) * row]:
                last -= 1
        self._last = data
        if self.stride == row:
            os.pwrite(self._fd, data[first * row:(last + 1) * row], first * self.stride)
        else:
            for y in range(first, last + 1):
                os.pwrite(self._fd, data[y * row:(y + 1) * row], y * self.stride)
        return last - first + 1

    def clear(self) -> None:
        from PIL import Image

        self._last = None
        self.show(Image.new("RGB", self.size, (0, 0, 0)))

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass


class PreviewDisplay:
    """Saves frames as PNG (for trying the face on a PC: python3 -m aira_pi demo)."""

    def __init__(self, out_dir: Path, size: tuple[int, int] = (480, 320)):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.size = size
        self.frames = 0

    def show(self, img, name: str | None = None) -> int:
        self.frames += 1
        img.save(self.out_dir / (name or f"frame-{self.frames:04d}.png"))
        return img.size[1]

    def clear(self) -> None:
        pass

    def close(self) -> None:
        pass


class NullDisplay:
    size = (480, 320)

    def show(self, img) -> int:
        return 0

    def clear(self) -> None:
        pass

    def close(self) -> None:
        pass

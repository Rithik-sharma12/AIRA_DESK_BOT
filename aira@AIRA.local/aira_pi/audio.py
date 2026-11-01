"""Optional voice on the Pi: I²S mic (INMP441) + DAC (PCM5102A), once they are wired.

Detects a real sound card (not HDMI/headphones) with ``arecord -l`` / ``aplay -l``.
Recording and playback use the alsa-utils programs, so no Python audio packages are
needed. Speech recognition and AIRA's voice still run on the laptop: the Pi records a
WAV, the laptop transcribes it (/api/v1/voice/transcribe) and returns spoken sentences
(/api/v1/voice/speak) that the Pi plays.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("aira_pi.audio")
EXCLUDED = re.compile(r"hdmi|vc4|bcm2835|headphones", re.I)
CARD_RE = re.compile(r"^card (\d+): (\S+) \[(.*?)\]", re.M)


def find_card(listing: str, wanted: str = "auto") -> str | None:
    for num, short, long_name in CARD_RE.findall(listing):
        if wanted not in ("auto", "") and wanted.lower() not in (short + " " + long_name).lower():
            continue
        if wanted == "auto" and EXCLUDED.search(short + " " + long_name):
            continue
        return short
    return None


def _list(cmd: str) -> str:
    exe = shutil.which(cmd)
    if not exe:
        return ""
    try:
        return subprocess.run([exe, "-l"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


class PiAudio:
    def __init__(self, setting: str = "auto"):
        self.capture = self.playback = None
        if setting == "off":
            return
        self.capture = find_card(_list("arecord"), setting)
        self.playback = find_card(_list("aplay"), setting)
        if self.capture or self.playback:
            log.info("Pi audio: microphone=%s speaker=%s", self.capture, self.playback)
        else:
            log.info("No I²S sound card yet: the Pi shows the face; the laptop/browser does mic and speaker")
        self._rec: asyncio.subprocess.Process | None = None
        self._rec_path: Path | None = None
        self._play: asyncio.subprocess.Process | None = None

    @property
    def can_listen(self) -> bool:
        return bool(self.capture)

    @property
    def can_speak(self) -> bool:
        return bool(self.playback)

    async def start_recording(self) -> None:
        await self.stop_playback()
        fd, name = tempfile.mkstemp(prefix="aira-", suffix=".wav")
        self._rec_path = Path(name)
        import os

        os.close(fd)
        self._rec = await asyncio.create_subprocess_exec(
            "arecord", "-q", "-D", f"plughw:CARD={self.capture}", "-f", "S16_LE", "-r", "16000", "-c", "1",
            "-d", "20", "-t", "wav", str(self._rec_path))

    async def stop_recording(self) -> bytes | None:
        if not self._rec or not self._rec_path:
            return None
        if self._rec.returncode is None:
            self._rec.terminate()
        await self._rec.wait()
        self._rec = None
        try:
            data = self._rec_path.read_bytes()
        finally:
            self._rec_path.unlink(missing_ok=True)
        return data if len(data) > 16000 else None   # under ~0.5 s: an accidental tap

    async def play(self, wav: bytes) -> None:
        if not self.playback:
            return
        self._play = await asyncio.create_subprocess_exec(
            "aplay", "-q", "-D", f"plughw:CARD={self.playback}", "-", stdin=asyncio.subprocess.PIPE)
        try:
            await self._play.communicate(wav)
        finally:
            self._play = None

    async def stop_playback(self) -> None:
        if self._play and self._play.returncode is None:
            self._play.terminate()

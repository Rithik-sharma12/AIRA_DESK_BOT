"""AIRA on the Raspberry Pi Zero 2 W: the face, captions, button and (later) voice.

The Pi is AIRA's body; the HP Omen laptop is her brain. This package finds the laptop on
the LAN, connects to it over an authenticated (and, with HTTPS, certificate-pinned)
WebSocket, and shows AIRA's face on the 3.5" ILI9486 screen. It needs only Debian
packages (python3-websockets, python3-pil, python3-numpy, python3-gpiozero).
"""

__version__ = "0.7.0"
RELEASE_DATE = "2026-11-01"      # AIRA Half: screen, touch menu, emotions, About, online AI

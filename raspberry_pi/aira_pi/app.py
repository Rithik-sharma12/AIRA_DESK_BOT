"""The AIRA Pi runtime: keep the face alive and stay linked to the laptop.

Three loops run side by side:
- render: draws the face (8 fps while animating, 4 fps otherwise; unchanged rows are skipped)
- link:   find the laptop (beacon or saved address), connect, follow its events, reconnect
- speech: (only with Pi audio) fetch AIRA's sentences from the laptop and play them in order
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__
from .audio import PiAudio
from .button import Button
from .config import PiConfig
from .face import FaceRenderer  # noqa: F401  (type of the simple face)
from .theme import create_renderer
from .net import HttpError, discover, http_bytes, http_json, multipart, pinned_context, ws_url
from .touch import TouchScreen, find_touch_device
from .touch_spi import SpiTouchScreen, open_spi_touch
from .online import ChatServer, OnlineBrain, OnlineError, base_url_for, new_code
from .controller import ControllerServer, new_token, run_beacon
from .protocol import EXPRESSIONS, envelope, parse

log = logging.getLogger("aira_pi")


def local_ips() -> list[str]:
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split()
        return [ip for ip in out if ":" not in ip]
    except (OSError, subprocess.SubprocessError):
        return []


def cpu_temp() -> float | None:
    try:
        return round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        return None


def wifi_name() -> str | None:
    try:
        return subprocess.run(["iwgetid", "-r"], capture_output=True, text=True, timeout=3).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def no_proxy(connect) -> dict:
    """Links on the local network never go through an HTTP proxy (websockets >= 15 would use one
    from the environment)."""
    import inspect

    try:
        return {"proxy": None} if "proxy" in inspect.signature(connect).parameters else {}
    except (TypeError, ValueError):
        return {}


async def ws_connect(url: str, token: str, ctx):
    """websockets >= 13 (asyncio API) with a fallback for older Debian releases."""
    headers = {"Authorization": f"Bearer {token}"}
    try:
        from websockets.asyncio.client import connect

        return await connect(url, additional_headers=headers, ssl=ctx, open_timeout=10, ping_interval=20,
                             max_size=1 << 20, **no_proxy(connect))
    except ImportError:
        import websockets

        return await websockets.connect(url, extra_headers=headers, ssl=ctx, open_timeout=10, ping_interval=20,
                                        max_size=1 << 20)


class PiApp:
    def __init__(self, cfg: PiConfig, display, face: FaceRenderer | None = None, audio: PiAudio | None = None):
        self.cfg = cfg
        self.display = display
        self.face = face or create_renderer(cfg.theme)
        if face is None and hasattr(self.face, "prewarm"):     # scale the mascot frames without delaying the face
            threading.Thread(target=self.face.prewarm, name="aira-prewarm", daemon=True).start()
        self.audio = audio or PiAudio(cfg.audio)
        self.ws = None
        self.laptop_url: str | None = None
        self.session_id: str | None = None
        self.server_tts: str | None = None
        self.started = time.monotonic()
        self._speech_q: asyncio.Queue[str] = asyncio.Queue()
        self._recording = False
        self._stop = asyncio.Event()
        self._hold_task: asyncio.Task | None = None
        self.exit_to_console = False
        self._reply_waiters: list[asyncio.Future] = []
        self.controller: ControllerServer | None = None
        self.brain = self._make_brain()
        if hasattr(self.face, "about_info"):
            self.face.about_info["edition"] = edition_name(cfg.edition)
        self.face.state.online_ai = self.brain is not None and cfg.brain in ("online", "auto")

    def _make_brain(self):
        if self.cfg.brain not in ("online", "auto") or not self.cfg.online_api_key:
            return None
        try:
            return OnlineBrain(base_url_for(self.cfg.online_service, self.cfg.online_base_url),
                               self.cfg.online_api_key, self.cfg.online_model)
        except OnlineError as exc:
            log.warning("Online AI not ready: %s", exc)
            return None

    @property
    def ctx(self):
        return pinned_context(self.cfg.cert_pem) if self.cfg.cert_pem else None

    # ================================================================== main
    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        button = Button(self.cfg.button_gpio, loop, lambda ev: loop.create_task(self.on_button(ev)))
        touch = self.start_touch(loop)
        tasks = [loop.create_task(self.render_loop()), loop.create_task(self.link_loop())]
        chat = await self.start_chat()
        joystick = await self.start_controller()
        if joystick:
            tasks.append(loop.create_task(run_beacon(self.cfg.controller_port, self.cfg.name, local_ips)))
        if self.audio.can_speak:
            tasks.append(loop.create_task(self.speech_loop()))
        try:
            await self._stop.wait()
        finally:
            for t in tasks:
                t.cancel()
            button.close()
            if touch:
                touch.close()
            if chat:
                await chat.close()
            if joystick:
                await joystick.close()
            self.display.clear()
            self.display.close()

    def stop(self) -> None:
        self._stop.set()

    # ============================================================ chat page
    async def start_chat(self) -> ChatServer | None:
        if not self.cfg.chat_page:
            return None
        if not self.cfg.chat_code:
            self.cfg.chat_code = new_code()
            try:
                self.cfg.save()
            except OSError as exc:
                log.warning("Cannot save the chat code: %s", exc)
        chat = ChatServer(self.ask_from_page, lambda: self.cfg.chat_code or "", self.cfg.chat_port)
        try:
            await chat.start()
        except OSError as exc:
            log.warning("Chat page not started (port %d): %s", self.cfg.chat_port, exc)
            return None
        return chat

    async def ask_from_page(self, text: str) -> tuple[str, str]:
        """Words typed on the chat page: to the laptop when it is AIRA's brain, else the online AI."""
        use_laptop = self.face.state.linked and self.ws is not None and self.cfg.brain != "online"
        if use_laptop:
            waiter = asyncio.get_running_loop().create_future()
            self._reply_waiters.append(waiter)
            await self.send("USER_TEXT", {"text": text})        # the laptop shows it, thinks, and captions back
            try:
                return await asyncio.wait_for(waiter, timeout=90), "laptop"
            except asyncio.TimeoutError:
                raise OnlineError("The laptop did not answer in time.") from None
            finally:
                if waiter in self._reply_waiters:
                    self._reply_waiters.remove(waiter)
        if self.brain is None:
            raise OnlineError("The laptop is not connected and no online AI is set up "
                              "(on AIRA: python3 -m aira_pi online setup)")
        self.face.set_caption(text, "user")
        self.face.set_expression("thinking")
        try:
            reply = await asyncio.to_thread(self.brain.ask, text)
        except OnlineError as exc:
            self.face.set_expression("error", hold_s=4)
            self.face.set_caption(str(exc), "system", 10)
            raise
        self.face.set_caption(reply or "...", "aira", 20)
        self.face.set_expression("speaking", hold_s=min(30.0, len(reply) / 14 + 0.5))
        return reply, "online"

    # ================================================================= touch
    def start_touch(self, loop) -> TouchScreen | SpiTouchScreen | None:
        if self.cfg.touch == "off" or not hasattr(self.face, "tap"):
            return None
        def tapped(x: int, y: int) -> None:
            loop.call_soon_threadsafe(lambda: loop.create_task(self.on_tap(x, y)))

        if self.cfg.touch in ("auto", "spi"):            # direct SPI reader (touch_spi.sh enable)
            chip = open_spi_touch()
            if chip is not None:
                touch = SpiTouchScreen(chip, self.cfg.touch_cal, tapped, threshold=self.cfg.touch_spi_threshold)
                touch.start()
                self._touch_label("XPT2046 over SPI")
                return touch
            if self.cfg.touch == "spi":
                log.warning("touch=spi but /dev/spidev0.1 is not ready: run  sudo bash touch_spi.sh enable")
                return None
        dev = find_touch_device(self.cfg.touch_device)
        if dev is None:
            log.info("No touch screen found (fine: the button and the laptop still drive AIRA)")
            return None
        touch = TouchScreen(dev, self.cfg.touch_cal, tapped)
        touch.start()
        self._touch_label(f"{dev.name} (kernel)")
        return touch

    def _touch_label(self, text: str) -> None:
        if hasattr(self.face, "about_info"):
            self.face.about_info["touch"] = text + ("" if self.cfg.touch_cal else ", not calibrated")

    # ============================================================ controller
    async def start_controller(self) -> ControllerServer | None:
        if not self.cfg.controller or not hasattr(self.face, "navigate"):
            return None
        if not self.cfg.controller_token:
            self.cfg.controller_token = new_token()
            try:
                self.cfg.save()
            except OSError as exc:
                log.warning("Cannot save the controller token: %s", exc)
        server = ControllerServer(self.on_controller, lambda: self.cfg.controller_token or "",
                                  self.cfg.controller_port, self.cfg.name, self._controller_changed)
        try:
            await server.start()
        except OSError as exc:
            log.warning("Controller server not started (port %d): %s", self.cfg.controller_port, exc)
            return None
        self.controller = server
        return server

    def _controller_changed(self, device: str, connected: bool) -> None:
        self.face.notify("Joystick connected" if connected else "Joystick disconnected", 3)

    async def on_controller(self, event: str, direction: str | None) -> None:
        """Joystick: arrows move the focus, press selects, long press goes home."""
        if event == "navigate":
            self.face.navigate(direction)
        elif event == "back":
            self.face.back()
        elif event == "select":
            await self.run_action(self.face.select())

    async def on_tap(self, x: int, y: int) -> None:
        await self.run_action(self.face.tap(x, y))

    async def run_action(self, action: str | None) -> None:
        """What a tap or a joystick press asked for: words for the laptop, or a Pi action."""
        if not action:
            return
        if action.startswith("say:"):
            if self.ws is None or not self.face.state.linked:
                self.face.notify("Laptop not connected")
                return
            await self.send("USER_TEXT", {"text": action[4:]})
        elif action == "sys:console":
            await self._console_after(0)
        elif action in ("sys:reboot", "sys:poweroff"):
            await self.power(action[4:])

    async def power(self, what: str) -> None:
        """Restart or shut down the Pi (allowed for this user by install.sh's polkit rule)."""
        self.face.notify("Restarting..." if what == "reboot" else "Shutting down... bye!", 10)
        await asyncio.sleep(1.5)                      # let the message reach the screen
        try:
            proc = await asyncio.create_subprocess_exec("systemctl", what, stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            ok = proc.returncode == 0
        except OSError as exc:
            ok, err = False, str(exc).encode()
        if not ok:
            log.error("systemctl %s failed: %s", what, err.decode(errors="replace").strip()[:200])
            self.face.notify("Not allowed: run install.sh again")

    # ================================================================ render
    async def render_loop(self) -> None:
        fast, slow = 1 / max(1, self.cfg.fps), 0.25
        info_at = 0.0
        while True:
            t0 = time.monotonic()
            if t0 - info_at > 10 and getattr(self.face, "screen", None) == "system" and t0 >= self.face.state.info_until:
                info_at = t0                       # keep the System screen's details fresh (IP, Wi-Fi, link)
                self.face.state.info_lines = await asyncio.to_thread(self.info_lines)
            img = self.face.render(t0)
            await asyncio.to_thread(self.display.show, img)
            delay = fast if self.face.animating(t0) else slow
            await asyncio.sleep(max(0.01, delay - (time.monotonic() - t0)))

    # ================================================================== link
    async def link_loop(self) -> None:
        backoff = 1.0
        while True:
            if not self.cfg.paired:
                self.face.state.status_text = "not paired"
                self.face.set_caption("Not paired yet. On the Pi run: python3 -m aira_pi pair", "system", 3600)
                await asyncio.sleep(10)
                self.cfg = PiConfig.load()   # pairing in another terminal writes the config
                continue
            self.face.state.status_text = "looking for the laptop"
            url = await self.find_laptop()
            if not url:
                await asyncio.sleep(min(backoff, 15))
                backoff = min(backoff * 2, 15)
                continue
            try:
                await self.session(url)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, laptop restarted, auth problems
                status = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
                if status in (401, 403):
                    self.face.set_caption("The laptop refused this Pi. Pair again: python3 -m aira_pi pair",
                                          "system", 3600)
                    log.error("laptop rejected the device token (HTTP %s)", status)
                    await asyncio.sleep(30)
                else:
                    log.info("link to %s lost: %s", url, exc.__class__.__name__)
            finally:
                self.ws = None
                self.face.state.linked = False
            await asyncio.sleep(min(backoff, 15))
            backoff = min(backoff * 2, 15)

    async def find_laptop(self) -> str | None:
        beacon = await discover(self.cfg.discovery_port, timeout=6.0, fingerprint=self.cfg.fingerprint)
        if beacon:
            if beacon.url.startswith("https") != bool(self.cfg.cert_pem):
                log.warning("Laptop at %s changed between HTTP and HTTPS; pair again if it won't connect", beacon.url)
            return beacon.url
        return self.cfg.laptop_url

    async def session(self, url: str) -> None:
        ws = await ws_connect(ws_url(url), self.cfg.token or "", self.ctx if url.startswith("https") else None)
        self.ws, self.laptop_url = ws, url
        try:
            await self.send("DEVICE_HELLO", {"name": self.cfg.name, "version": __version__,
                                             "caps": {"audio": self.audio.can_speak and self.audio.can_listen,
                                                      "display": "480x320", "button": True,
                                                      "edition": edition_name(self.cfg.edition)}})
            hb = asyncio.get_running_loop().create_task(self.heartbeat())
            try:
                async for raw in ws:
                    frame = parse(raw)
                    if frame:
                        await self.handle(frame)
            finally:
                hb.cancel()
        finally:
            with contextlib.suppress(Exception):
                await ws.close()

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(20)
            await self.send("DEVICE_HEARTBEAT", {"uptime_s": round(time.monotonic() - self.started, 1),
                                                 "cpu_temp_c": cpu_temp()})

    async def send(self, msg_type: str, payload: dict[str, Any]) -> None:
        if self.ws is not None:
            await self.ws.send(envelope(msg_type, self.cfg.device_id, payload, self.session_id))

    # =============================================================== events
    async def handle(self, frame: dict[str, Any]) -> None:
        t, p = frame["type"], frame["payload"]
        if t == "SERVER_WELCOME":
            self.session_id = p.get("session_id")
            self.server_tts = p.get("tts")
            self.face.state.linked = True
            self.face.state.status_text = "linked"
            self.face.set_expression("happy", hold_s=2.5)
            self.face.notify(f"Connected to {str(p.get('host') or 'the laptop')[:24]}")
            log.info("Linked to %s (model %s)", self.laptop_url, p.get("model"))
        elif t == "DISPLAY_STATE_CHANGED":
            expr = p.get("expression", "idle")
            if expr in EXPRESSIONS and not self._recording:
                self.face.set_expression(expr, hold_s=p.get("hold_s"))
        elif t == "CAPTION":
            self.face.set_caption(str(p.get("text", "")), "user" if p.get("role") == "user" else "aira")
            if p.get("role") != "user" and self._reply_waiters:      # answer for the Pi's chat page
                waiter = self._reply_waiters.pop(0)
                if not waiter.done():
                    waiter.set_result(str(p.get("text", "")))
        elif t == "REMINDER":
            self.face.flash(8)
            self.face.set_caption(str(p.get("say") or p.get("text", "")), "system", 30)
            if self.audio.can_speak:
                await self._speech_q.put(str(p.get("say") or p.get("text", "")))
        elif t == "CONFIRM_REQUEST":
            self.face.set_caption(str(p.get("prompt", "")), "aira", 60)
        elif t == "SHOW_SCREEN":
            show = getattr(self.face, "show_screen", None)       # only the touch-menu theme has screens
            if show and show(str(p.get("screen", ""))):
                log.info("Screen %s (from the web page)", p.get("screen"))
        elif t == "SAY" and self.audio.can_speak:
            await self._speech_q.put(str(p.get("text", "")))
        elif t == "ERROR":
            self.face.set_expression("error", hold_s=4)
            self.face.set_caption(str(p.get("message", "Something went wrong.")), "system", 10)

    # =============================================================== button
    async def on_button(self, ev: str) -> None:
        if ev == "hold":
            if self._recording:
                self._recording = False
                await self.audio.stop_recording()
            self.face.show_info(self.info_lines() + ["Keep holding 5 s: text console"], 12)
            self._hold_task = asyncio.get_running_loop().create_task(self._console_after(4.5))
            return
        if ev in ("up", "hold_released", "down") and self._hold_task:
            self._hold_task.cancel()
            self._hold_task = None
            if ev == "hold_released":
                return
        if not self.face.state.linked:
            self.face.show_info(self.info_lines(), 8)
            return
        if self.audio.can_listen:
            if ev == "down":
                self._recording = True
                self.face.set_expression("listening")
                await self.audio.start_recording()
                await self.send("LISTENING_STARTED", {})
            elif ev == "up" and self._recording:
                self._recording = False
                self.face.set_expression("thinking")
                wav = await self.audio.stop_recording()
                await self.send("LISTENING_STOPPED", {})
                if wav:
                    await self.transcribe_and_send(wav)
                else:
                    self.face.set_expression("idle")
        elif ev == "up":
            await self.send("BUTTON_EVENT", {"action": "press"})
            self.face.set_caption("Listening on the laptop/phone page...", "system", 4)

    async def _console_after(self, seconds: float) -> None:
        """Very long press: hand the screen back to the Linux text console (and the phone keyboard)."""
        await asyncio.sleep(seconds)
        self.face.show_info(["Switching to the text console.", "", "To bring AIRA's face back:",
                             "  sudo systemctl start aira-pi", "or just reboot."], 3)
        await asyncio.sleep(2.5)
        self.exit_to_console = True
        self.stop()

    async def transcribe_and_send(self, wav: bytes) -> None:
        body, ctype = multipart({"language": "auto"}, "audio", "utterance.wav", wav)
        try:
            raw, _ = await asyncio.to_thread(http_bytes, self.laptop_url, "POST", "/api/v1/voice/transcribe", body,
                                             ctype, token=self.cfg.token, ctx=self.ctx)
        except (HttpError, OSError) as exc:
            self.face.set_expression("error", hold_s=3)
            msg = "Speech recognition isn't set up on the laptop." if getattr(exc, "status", 0) == 501 \
                else "I couldn't hear that, sorry."
            self.face.set_caption(msg, "system", 6)
            return
        text = (json.loads(raw).get("text") or "").strip()
        if text:
            await self.send("USER_TEXT", {"text": text})
        else:
            self.face.set_expression("confused", hold_s=2)
            self.face.set_caption("Didn't catch that.", "system", 4)

    # =============================================================== speech
    async def speech_loop(self) -> None:
        while True:
            sentence = await self._speech_q.get()
            if not sentence.strip() or not self.laptop_url:
                continue
            try:
                wav, _ = await asyncio.to_thread(http_bytes, self.laptop_url, "POST", "/api/v1/voice/speak",
                                                 json.dumps({"text": sentence}).encode(),
                                                 "application/json", token=self.cfg.token, ctx=self.ctx)
            except (HttpError, OSError) as exc:
                log.info("no laptop voice (%s); showing text only", exc)
                continue
            self.face.set_expression("speaking")
            await self.send("SPEECH_STARTED", {})
            await self.audio.play(wav)
            if self._speech_q.empty():
                self.face.set_expression("idle")
                await self.send("SPEECH_FINISHED", {})

    # ================================================================= info
    def info_lines(self) -> list[str]:
        """Most useful first: the System screen shows the first six."""
        ips = local_ips()
        link = "connected" if self.face.state.linked else ("not paired" if not self.cfg.paired else "searching")
        brain = {"laptop": "laptop", "online": "online AI", "auto": "laptop, else online AI"}.get(self.cfg.brain, "?")
        lines = [f"Edition: {edition_name(self.cfg.edition)} ({EDITION_NOTE.get(self.cfg.edition, '')})",
                 f"Pi: {socket.gethostname()}  {', '.join(ips) or 'no network'}  Wi-Fi: {wifi_name() or '-'}"]
        if ips and self.cfg.chat_page:
            lines.append(f"Chat page: http://{ips[0]}:{self.cfg.chat_port}  code {self.cfg.chat_code or '-'}")
        joy = self.controller
        lines += [f"Laptop: {link}  {self.laptop_url or ''}".rstrip()]
        if self.cfg.controller:
            lines.append(f"Joystick: {'connected' if joy and joy.connected else 'waiting'} "
                         f"(port {self.cfg.controller_port})")
        lines += [f"Brain: {brain}" + (f" ({self.cfg.online_model})" if self.brain else ""),
                  f"Voice on Pi: {'yes' if self.audio.can_speak else 'not wired yet'}"]
        t = cpu_temp()
        lines.append(f"CPU {t} C  |  aira_pi {__version__}" if t else f"aira_pi {__version__}")
        return lines


EDITION_NOTE = {"half": "screen only; talk on the web page", "full": "mic, speaker and battery"}


def edition_name(edition: str) -> str:
    """'half' -> 'AIRA Half' (this build: just the screen), 'full' -> 'AIRA Full' (the complete handset)."""
    return "AIRA Full" if str(edition).lower() == "full" else "AIRA Half"


# ===================================================================== pairing
async def pair(cfg: PiConfig, laptop: str | None, code: str | None, name: str | None) -> int:
    from .net import fetch_certificate, pretty_fingerprint

    url = laptop.rstrip("/") if laptop else None
    if not url:
        print("Looking for the AIRA laptop on this network (15 s)...")
        print("  (the laptop server must run with AIRA_ALLOW_LAN=true)")
        beacon = await discover(cfg.discovery_port, timeout=15)
        if not beacon:
            print("Not found. Start the laptop server, check both are on the same hotspot, or pass\n"
                  "  --laptop https://<laptop-ip>:8765")
            return 1
        url = beacon.url
        print(f"Found {beacon.host} at {url}")
    cert_pem = fp = None
    if url.startswith("https"):
        cert_pem, fp = await asyncio.to_thread(fetch_certificate, url)
        print(f"Laptop certificate fingerprint: {pretty_fingerprint(fp)}")
        print("  It should match the start of the 'Certificate fingerprint' line in the laptop's server window.")
    else:
        print("Warning: plain HTTP. The token travels unencrypted on the hotspot. HTTPS is recommended "
              "(HOW-TO-RUN.md section 4).")
    ctx = pinned_context(cert_pem) if cert_pem else None
    try:
        await asyncio.to_thread(http_json, url, "POST", "/api/v1/web/hello", {}, ctx=ctx)
    except (HttpError, OSError) as exc:
        print(f"Cannot reach {url}: {exc}")
        return 1
    if not code:
        code = input("Enter the 6-digit pairing code shown in the laptop's server window: ")
    code = "".join(ch for ch in code if ch.isdigit())
    try:
        res = await asyncio.to_thread(http_json, url, "POST", "/api/v1/web/pair",
                                      {"code": code, "name": (name or cfg.name)[:40]}, ctx=ctx)
    except HttpError as exc:
        print(f"Pairing failed: {exc}")
        return 1
    cfg.token, cfg.cert_pem, cfg.fingerprint, cfg.laptop_url = res["token"], cert_pem, fp, url
    if name:
        cfg.name = name[:40]
    path = cfg.save()
    print(f"Paired. Saved to {path}.")
    print("If the AIRA face service is running, restart it:  sudo systemctl restart aira-pi")
    return 0

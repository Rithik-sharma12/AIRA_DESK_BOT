# AIRA — Autonomous Intelligent Responsive Assistant

A physical desktop AI companion with a pixel-art girl mascot. The **HP Omen 16** laptop is
her brain (AI model, speech, laptop skills); a **Raspberry Pi Zero 2 W** with a 3.5" ILI9486
screen is her face. She speaks English, Tamil and Tanglish in a sweet young girl's voice.

| | |
|---|---|
| Current build | **AIRA Half**: aira_pi 0.7.0, released 1 Nov 2026 |
| Developer | Rajendhira Prasath Saravanan · SODAK Technology |
| Laptop | HP Omen 16, Ryzen 7, RTX 4060 8 GB, 16 GB RAM, Windows 11 |
| Pi | Raspberry Pi Zero 2 W (4 x Cortex-A53, 512 MB), Debian 13 (trixie), kernel 6.18 |
| Screen | 3.5" ILI9486 480x320 SPI TFT (`tft35a` overlay, `/dev/fb1`), XPT2046 touch |

The full engineering design (architecture, technology choices, roadmap, threat model,
shopping list) is in **docs/DESIGN.md**; what has been verified on the real hardware is in
**docs/PROJECT_STATE.md**.

## What AIRA does today

- **Talks** on a web page (laptop or phone browser): speak or type, in English, Tamil or Tanglish.
- **Thinks** with a local model on the laptop (Ollama, `qwen3.5:9b`: about 40 tokens/s, first word in about 0.7 s), or an online AI with your own API key, or both (local first, online as backup).
- **Does things on the laptop**: open apps and websites, volume and media keys, brightness, laptop status, timers and reminders, lock; risky actions (shut down, close apps) ask first.
- **Speaks** with a sweet girl's voice: offline Piper voices raised 3.5 semitones (voice "A"), or Azure neural voices if you add a key. Symbols are spoken as words ("24°C" → "24 degrees Celsius"); no markdown, emoji or links read aloud.
- **Shows her face on the Pi**: pixel-art mascot with 9 emotions, blinking and a moving mouth, her words as subtitles, and the screens Home, Chat, Music, Tools, Emotions, About and System.
- **Is controlled without touching the Pi**: from the web page's "AIRA's screen" buttons, or an ESP32 wireless joystick.
- **Works on the Pi alone** (optional): with an online AI key on the Pi, you chat from your phone on the Pi's own chat page.

## Editions

| Edition | What it is | You talk to AIRA |
|---|---|---|
| **AIRA Half** (now) | Laptop brain + the Pi with only its 3.5" screen: no microphone, speaker or battery yet | On the web page; the Pi shows her face, state and words |
| **AIRA Full** (goal) | The complete handset: I²S microphone and speaker, battery and USB-C charging, in its case | On the handset itself; the web page still works |

The Pi says which it is (`"edition"` in its config: `half` or `full`); its About and System
screens and the web page's top bar (**AIRA HALF · ON**) show it.

## AI brain: local or online

| Mode | AI brain | Set in |
|---|---|---|
| **Local** (default) | Ollama on the laptop: private, free, works offline | `.env`: `AIRA_LLM_PROVIDER=ollama` |
| **Online** | any OpenAI-compatible service with your API key: OpenAI, Gemini, Groq, OpenRouter, DeepSeek, Mistral, Together, Anthropic | `.env`: `AIRA_LLM_PROVIDER=online` + `AIRA_ONLINE_SERVICE`, `AIRA_ONLINE_MODEL`, `AIRA_ONLINE_API_KEY` |
| **Auto** | local first; online whenever Ollama is not running (switches back after a minute) | `.env`: `AIRA_LLM_PROVIDER=auto` |
| **Pi alone** | the Pi asks the online AI itself; you type on its chat page `http://<pi>:8770/` | on AIRA: `python3 -m aira_pi online setup` |

The model stays loaded in the GPU while AIRA runs (`AIRA_LLM_KEEP_ALIVE=-1`), so no reply waits
for a reload. With an online AI, what you say is sent to that company; the local mode keeps
everything on the laptop. Every setting, for the laptop and the Pi: **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)**.

## Quick start

### Laptop (Windows PowerShell, in the project folder)

```powershell
winget install Ollama.Ollama                 # if not installed
ollama pull qwen3.5:9b                       # the model AIRA uses for task automation
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1                  # venv, dependencies, .env with an API key
.\.venv\Scripts\Activate.ps1
python scripts\download_voices.py            # offline girl voices (English + Tamil)
python scripts\check_environment.py          # must show 0 FAIL
.\scripts\run_server.ps1                     # starts Ollama if needed, then AIRA
```

Open the address the server window prints (`https://localhost:8765/`), accept the
certificate warning once, and pair with the code shown in the server window. Full guide,
phone use and troubleshooting: **[HOW-TO-RUN.md](HOW-TO-RUN.md)**.

### Raspberry Pi

```powershell
.\scripts\connect_pi.ps1 -Fix                # as Administrator: .env, certificate, firewall, Private network
.\scripts\connect_pi.ps1 -Deploy -Pair       # copy the Pi folder over SSH and pair (server running)
```
Then **on AIRA**: `cd ~/raspberry_pi && sudo bash install.sh` (first time), and
`sudo systemctl restart aira-pi` after updates. Everything about the Pi:
**[raspberry_pi/README.md](raspberry_pi/README.md)**.

### ESP32 joystick (optional)

KY-023 on an ESP32 WROOM (VRX GPIO32, VRY GPIO33, SW GPIO27, 3.3 V), same 2.4 GHz hotspot.
Token from `python3 -m aira_pi controller` on AIRA; firmware, wiring and three test stages:
**[esp32_joystick/README.md](esp32_joystick/README.md)**.

## The Pi screen

| Screen | Shows |
|---|---|
| **Home** | AIRA large (271x216) with her state, the time, Pi CPU / RAM / temperature, and her words underneath; a sleepy clock when nobody is connected |
| **Chat** | the latest messages, English and Tamil |
| **Music** | previous, play / pause, next, volume down, mute, volume up (the laptop's) |
| **Tools** | laptop status, 5-minute timer, reminders, lock, open Chrome, open VS Code |
| **Emotions** | try her 9 emotions (idle, happy, excited, listening, thinking, speaking, confused, sleepy, error); AUTO PLAY shows each for 3 s |
| **About** | version, release date, developer, and the Pi's board, CPU, RAM, storage, OS, kernel, screen, touch, uptime (read live) |
| **System** | Pi stats, network, chat page code, laptop link, joystick, Text console / Restart / Shut down (these two ask twice) |

How to move between them:

| Control | How |
|---|---|
| Web page | "AIRA's screen (Pi)" buttons on a wide screen show any screen for a minute; **Emotions** plays them all |
| ESP32 joystick | up / down through the menu, right into the buttons, press = select, long press = Home; a magenta ring shows the focus |
| Touch | **not working on this screen**: the XPT2046 touch controller does not answer at all (tested 1 Nov; SPI pins verified). Hardware fault suspected; a replacement 3.5" (A) screen needs no software change. `sudo bash raspberry_pi/touch_spi.sh test` checks any screen |

## Status

✅ verified on the real hardware · 🔧 built and tested in software, not yet on the hardware · ⏳ planned

| Area | State |
|---|---|
| AI brain on the laptop (Ollama `qwen3.5:9b`, FastAPI, streaming) | ✅ benchmark passed: 42 tokens/s, 100% GPU |
| Web page: voice and text, girl voice, neo-brutalist design, animated mascot | ✅ in use |
| Laptop task automation (apps, media, status, timers, reminders, confirmations) | 🔧 tested with a dry-run backend; try it on Windows |
| Pi screen: mascot, captions, 7 screens | ✅ face and interface shown on the TFT (1 Nov) · 🔧 Emotions / About new |
| Pi ↔ laptop link: discovery, HTTPS pinning, pairing, reconnect | ✅ paired and connected (1 Nov) |
| Online AI (laptop: online / auto) and Pi-alone chat page | 🔧 tested against simulated services |
| ESP32 joystick | 🔧 Pi side tested end to end with the keyboard stand-in; firmware compiles for ESP32 |
| Touch | ❌ touch controller not responding (hardware) |
| I²S microphone, speaker, battery (AIRA Full) | ⏳ parts planned: docs/HARDWARE.md |
| Long-term memory, wake word | ⏳ planned |

Next direction under review: an OpenClaw-based agent backend with a custom desktop companion
app (handoff document of 1 Nov 2026); the Pi stays a lightweight display client either way.

## Repository layout

```
laptop_server/      AI brain (runs on the HP Omen)
  config.py         all settings, env-overridable (AIRA_*), see docs/CONFIGURATION.md
  app.py, main.py   FastAPI app factory and entry point (python -m laptop_server)
  llm/              Ollama (local), online (any OpenAI-compatible API, your key), auto (local first)
  agent/            persona, conversation, agent engine (instant commands, tool calling,
                    confirmations), tools/ (apps, web, status, media, timers, power), scheduler
  api/              REST + SSE routes, web voice routes (pairing, transcribe, speak), Pi link
  web/              the browser interface (single page)
  security/         API-key auth, browser + device pairing
  events.py         live events (state, captions, reminders, Pi screen) for web pages and the Pi
  discovery.py      LAN beacon so the Pi finds the laptop on a hotspot
  monitoring/       JSON logs, audit log, rolling latency metrics
  voice/            Piper (offline) + Azure TTS with fallback, girl voice pitch, speech text cleanup,
                    faster-whisper STT, Tamil/English script splitting
shared/protocol/    versioned JSON messages shared by the laptop and the Pi (v1.2)
raspberry_pi/       EVERYTHING for the Pi: copy this folder to it (raspberry_pi/README.md)
  aira_pi/          the Pi app: screens, mascot, laptop link, touch, joystick server, online AI +
                    chat page, About / system info
  install.sh        installs the aira-pi service (Debian packages only)
  touch_spi.sh      reads the touch chip directly over SPI (test / enable / disable)
  setup_audio.sh    I²S audio setup with backups and undo (for AIRA Full)
  wifi_keyboard/    phone-as-keyboard for the Pi's text console
  check_hardware.py, collect_baseline.sh   hardware checks and a read-only system report
esp32_joystick/     ESP32 + KY-023 wireless joystick: firmware, secrets template, wiring, setup
scripts/            setup_windows, run_server, connect_pi (Pi link check / deploy / pair),
                    check_environment, benchmark_llm, chat_cli, make_dev_cert (HTTPS),
                    download_voices, voice_preview, generate_api_key, mascot sprite tools,
                    bakeoff/ (voice comparison)
config/             apps.example.json: add your own apps and websites for AIRA to open
tests/              unit/, integration/ (Ollama emulator, real browser, Pi link), hardware/
docs/               CONFIGURATION, DESIGN, PROJECT_STATE, HARDWARE, INSTALLATION, API,
                    PHASE1_ACCEPTANCE, PHASE2_VOICE_BAKEOFF, journal/ (setup logs)
```

## Documentation

| Read | For |
|---|---|
| [HOW-TO-RUN.md](HOW-TO-RUN.md) | running AIRA on the laptop and phone, voices, models, troubleshooting |
| [raspberry_pi/README.md](raspberry_pi/README.md) | the Pi: install, pairing, screens, touch checks, Pi-alone online AI, joystick, audio |
| [esp32_joystick/README.md](esp32_joystick/README.md) | building and connecting the joystick |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every setting on the laptop and the Pi |
| [docs/PROJECT_STATE.md](docs/PROJECT_STATE.md) | what is verified on the hardware, and what is next |
| [docs/HARDWARE.md](docs/HARDWARE.md) | parts, wiring, GPIO plan, bring-up steps |
| [docs/API.md](docs/API.md) | the server's REST, SSE and WebSocket API |
| [docs/DESIGN.md](docs/DESIGN.md) | architecture and design decisions |
| [docs/PHASE2_VOICE_BAKEOFF.md](docs/PHASE2_VOICE_BAKEOFF.md) | comparing speech engines |

Run the tests with `python -m pytest` (unit, integration with an Ollama emulator, a real
browser and a simulated Pi).

![AIRA hardware connections](docs/images/aira_wiring.svg)

## Security and privacy

- The server listens only on the laptop unless `AIRA_HOST=0.0.0.0` and `AIRA_ALLOW_LAN=true`; use HTTPS (`scripts\make_dev_cert.py`) on the network and never forward port 8765 on a router.
- Every endpoint except `/api/v1/health` needs the API key; browsers and the Pi pair with a one-time code shown in the server window, and the Pi pins the laptop's certificate.
- The joystick and the Pi's chat page need their own codes (shown on the Pi); wrong codes are rate-limited and lock out.
- API keys live only in `.env` (laptop) and `~/.config/aira-pi/config.json` (Pi, only if you set up the Pi's own online AI); the ESP32's Wi-Fi password and token live in its git-ignored `secrets.h`. None are logged or shown.
- Conversation text is not written to logs unless `AIRA_LOG_CONVERSATION_CONTENT=true`; failed authentication goes to `logs/audit.jsonl`. Keep Ollama on localhost (do not set `OLLAMA_HOST=0.0.0.0`).
- Laptop actions come from a fixed allowlist of tools (no shell access for the model); risky ones need your yes.

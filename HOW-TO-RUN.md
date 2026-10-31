# How to run AIRA's web voice interface (prototype)

Talk to AIRA from any browser — the laptop itself, your phone or another computer — using
**that device's microphone and speaker**. The AI runs on the HP Omen; the browser only
records your voice and plays AIRA's reply.

```
Browser (laptop / phone)                         HP Omen 16 (AIRA server)
  🎤 microphone ── recorded clip ───────────────► speech-to-text (Whisper)  ─┐
                                                                             ▼
  💬 chat bubbles ◄── reply, streamed word by word ── Gemma via Ollama ◄─────┘
  🔊 speaker ◄─── each sentence as audio ◄────────── text-to-speech (Azure / Piper)
```

Every speech engine is optional. If the laptop has no Whisper or no voice engine yet, the
page uses the browser's own speech recognition and voices instead, so you can start
in a few minutes and upgrade later.

All commands are **Windows PowerShell on the HP Omen**, run from the repository folder
(`E:\Github Repo\AIRA_DESK_BOT`).

---

## 1. One-time setup

You need: Python 3.12 (or 3.11), Ollama for Windows, and Chrome or Edge.

```powershell
cd "E:\Github Repo\AIRA_DESK_BOT"
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1          # makes .venv, installs packages, creates .env with an API key
ollama pull gemma4:e4b-it-qat        # the AI model (about 6 GB)
.\.venv\Scripts\Activate.ps1
python scripts\check_environment.py  # must end with "0 fail"
```

Already set up before? Update the packages after pulling the latest code:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

If `ollama pull` says the tag is not found, run `ollama list` / see ollama.com/library/gemma4
and put the exact tag in `.env` as `AIRA_LLM_MODEL=...`.

## 2. Start AIRA and talk to it on the laptop (5 minutes)

```powershell
.\scripts\run_server.ps1
```

The window prints, among other lines:

```
Web voice interface: http://localhost:8765/
Web UI: speech recognition on the browser, speech output on the browser
Web UI pairing code: 123 456
```

1. Open **http://localhost:8765** in Chrome or Edge on the same laptop.
2. Enter the 6-digit **pairing code** from the server window and press **Pair**. The browser
   remembers it; you pair each browser once.
3. Allow the microphone when the browser asks.
4. Tap **🎤** (or press the space bar), speak, then pause — it stops by itself after about a
   second of silence. AIRA's reply appears word by word and is spoken sentence by sentence.
5. Tap **🎤** while AIRA is speaking to interrupt her. Or type in the box and press Enter.

`localhost` counts as a secure page, so the microphone works without HTTPS here.

> **Privacy note:** in this "browser" mode, Chrome and Edge send your voice to Google's or
> Microsoft's online speech service. Step 3 keeps speech recognition on your laptop.

## 3. Better voice: run speech on the laptop

### 3a. Speech recognition with Whisper (offline, uses the RTX 4060)

```powershell
pip install -r requirements-voice.txt
```

Restart the server. The window now says `speech recognition on faster-whisper`. The first
time you speak, it downloads the model (`large-v3-turbo`, about 1.6 GB) and loads it, so the
first reply is slow; later ones are fast.

If the log shows a CUDA / cuDNN / cuBLAS error, the GPU libraries that faster-whisper needs
are missing (see the faster-whisper README, "GPU" section). Until then, run it on the CPU with
a smaller model by adding to `.env`:

```
AIRA_STT_DEVICE=cpu
AIRA_STT_COMPUTE_TYPE=int8
AIRA_STT_MODEL=small
```

Settings → *I'll speak* tells Whisper the language: *Auto*, *English* or *Tamil / Tanglish*.
Choosing Tamil helps when it mishears Tamil as English.

### 3b. AIRA's own voice (Azure or Piper)

Without this, AIRA uses a voice built into the browser or phone (quality varies; Tamil
voices exist on most Android phones).

- **Azure (best Tamil quality, online):** create a *Speech* resource in the Azure portal,
  then put its key and region in `.env`:
  ```
  AIRA_AZURE_SPEECH_KEY=<key 1>
  AIRA_AZURE_SPEECH_REGION=centralindia
  ```
  AIRA then speaks with `ta-IN-PallaviNeural` (Tamil and Tanglish) and `en-IN-NeerjaNeural`
  (English-only sentences). Adjust `AIRA_TTS_RATE` and `AIRA_TTS_PITCH_PCT` to taste.
- **Piper (offline, CPU):** install `piper-tts` (in `requirements-voice.txt`), download a
  Tamil voice (`.onnx` + `.onnx.json`, see docs/PHASE2_VOICE_BAKEOFF.md) and set
  `AIRA_TTS_PIPER_MODEL=models/piper/ta_IN-rasa_female-medium.onnx`.

Restart the server after changing `.env`. Settings → *AIRA's voice* lets each browser choose
the laptop voice, the browser voice, or text only.

## 4. Use it from your phone (or another device)

Browsers only allow the microphone on **https://** pages (or `localhost`). Plain
`http://<laptop-ip>` will show the page, but the microphone stays blocked. So:

**a. Same network.** Connect the laptop and the phone to the same Wi-Fi, or connect the
laptop to the phone's hotspot. On the laptop: Settings → Network & internet → Wi-Fi → this
network → **Network profile type: Private**.

**b. Make an HTTPS certificate** (re-run it when the laptop's address changes):

```powershell
python scripts\make_dev_cert.py
```

**c. Add these lines to `.env`** (the script prints the last two):

```
AIRA_HOST=0.0.0.0
AIRA_ALLOW_LAN=true
AIRA_SSL_CERTFILE=certs/aira-dev.crt
AIRA_SSL_KEYFILE=certs/aira-dev.key
```

**d. Restart the server.** When Windows asks whether Python may use the network, allow
**Private networks** only. If it never asks and the phone can't connect, add a rule once
(PowerShell **as Administrator**):

```powershell
New-NetFirewallRule -DisplayName "AIRA web 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private
```

**e. On the phone**, open the `https://<laptop-ip>:8765/` address printed in the server
window. Chrome warns that the connection is not private, because the certificate is your
own: tap **Advanced → Proceed to … (unsafe)**. Then pair with the code from the server
window and allow the microphone.

If the phone still refuses the microphone, open `chrome://flags/#unsafely-treat-insecure-origin-as-secure`
on the phone, add `http://<laptop-ip>:8765`, set it to *Enabled*, relaunch Chrome, and use
that `http://` address instead.

## 5. Using the interface

| Control | What it does |
|---|---|
| 🎤 / space bar | Start listening; tap again to stop early. While AIRA speaks, it interrupts her |
| Text box + Enter | Type instead of speaking |
| ↺ | New conversation (AIRA forgets the current one) |
| ⚙ Settings | Speech recognition (Auto / Server / Browser), AIRA's voice (Auto / Server / Browser / Off), language, auto-stop, "keep listening after AIRA replies" for hands-free conversation, browser voice speed, *Forget this device* |
| Face | Blue blinking = idle · green = listening · purple = thinking · mouth moving = speaking · red = error |

Each bubble shows timings: how long speech recognition took, and how fast the model
answered (first word, tokens per second).

## 6. Checking it works (tests)

```powershell
python -m pytest
```

The real-browser test (pairs, records from a simulated microphone, plays the reply) runs only
when Playwright is installed:

```powershell
pip install playwright
python -m playwright install chromium
python -m pytest tests\integration\test_web_ui_browser.py -v
```

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| Page says the microphone is blocked | Use `localhost` on the laptop, or HTTPS from other devices (section 4) |
| "Microphone permission denied" | Browser site settings → Microphone → Allow for this address; on Windows also Settings → Privacy → Microphone |
| Status "AI model not ready" | Ollama not running or model missing: `ollama list`, then check the server window |
| Phone can't open the page | Different networks, network profile is *Public*, or firewall (section 4a/4d); check the address in the server window |
| Certificate error even after "Proceed" | The laptop's address changed: run `python scripts\make_dev_cert.py` again and restart |
| Wrong pairing code / "pairing locked" | The code changes after every successful pairing; after 5 wrong tries restart the server |
| Speech recognition misses Tamil | Settings → *I'll speak* → *Tamil / Tanglish*; use Whisper (3a) rather than browser mode |
| AIRA's voice sounds robotic or English-accented in Tamil | Set up Azure (3b), or pick another Tamil voice in the phone's text-to-speech settings |
| First spoken reply takes long | Whisper is loading; the next ones are fast. AI model warm-up also happens at server start |
| No sound | Phone media volume / silent mode; Settings → *AIRA's voice* not *Off* |

## 8. Security and privacy

- The server listens only on the laptop (`localhost`) unless you set `AIRA_HOST=0.0.0.0` and `AIRA_ALLOW_LAN=true`. Never forward port 8765 on a router.
- Every browser must pair with the code shown in the server window (someone must be at the laptop). Codes work once and lock after 5 wrong tries.
- Paired browsers are stored as hashes in `data/web_devices.json`. Delete that file (with the server stopped) to unpair all browsers. On a phone, use Settings → *Forget this device*.
- Audio you upload is processed in memory and a temporary file, then deleted. Conversation text is not written to logs unless `AIRA_LOG_CONVERSATION_CONTENT=true`.
- `data/`, `certs/` and `.env` are git-ignored: never commit them.

## 9. Stop

Press **Ctrl+C** in the server window.

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
(`E:\Github Repo\AIRA_DESK_BOT`), not from inside `scripts`.

> **Using Command Prompt (`cmd`) instead?** `.ps1` files don't run there. Use these equivalents:
>
> | PowerShell | Command Prompt |
> |---|---|
> | `.\scripts\setup_windows.ps1` | `powershell -ExecutionPolicy Bypass -File scripts\setup_windows.ps1` |
> | `.\.venv\Scripts\Activate.ps1` | `.venv\Scripts\activate.bat` |
> | `.\scripts\run_server.ps1` | `chcp 65001` then `python -m laptop_server` (venv active) |
>
> "The system cannot find the path specified" on activation means the setup step has not
> created `.venv` yet: run the setup line first.

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
4. Tap the teal **microphone** button (or press the space bar), speak, then pause — it stops by itself after about a
   second of silence. AIRA's reply appears word by word and is spoken sentence by sentence.
5. Tap the microphone while AIRA is speaking to interrupt her. Or type in the box and press Enter.

`localhost` counts as a secure page, so the microphone works without HTTPS here.

> **Privacy note:** in this "browser" mode, Chrome and Edge send your voice to Google's or
> Microsoft's online speech service. Step 3 keeps speech recognition on your laptop.

## 2b. Choosing a model (speed vs. quality on the RTX 4060, 8 GB)

AIRA switches off the model's hidden "thinking" step (`AIRA_LLM_THINK=false`, the default).
Gemma 4 and Qwen 3.5 think silently before every answer unless told not to. That made replies
take 10–15 s to start, and often come back **empty** because the thinking used up the
whole reply budget (`ttft=None` in the server log). With it off, replies should start in about a second.

Pick the model with one line in `.env`, then restart the server:

| `AIRA_LLM_MODEL=` | Download | Fits in 8 GB VRAM | Best for |
|---|---|---|---|
| `gemma4:e4b-it-qat` (default) | ~6 GB | yes | Tamil / Tanglish, the best all-rounder for AIRA |
| `qwen3.5:9b` | ~6.6 GB | yes, small context | Smartest English answers and reasoning; check its Tamil yourself |
| `qwen3.5:4b` or `gemma4:e2b` | ~4 GB | yes, lots of room | Fastest; use if Whisper also runs on the GPU and things feel slow |
| `gemma4:12b` | 8 GB | **no**, spills to the CPU | Not recommended for voice: noticeably slower |
| `qwen3:8b` (you may already have it) | 5.2 GB | yes | Quick test with no download; weaker Tamil |

Compare them on your own laptop. The benchmark asks the same English, Tamil, Tanglish, maths
and honesty questions, measures time-to-first-word, speed and VRAM use, and writes a report
with every model's answers side by side so you can judge the Tamil yourself:

```powershell
ollama pull gemma4:e4b-it-qat
ollama pull qwen3.5:9b
python scripts\benchmark_llm.py --runs 2
```

The report is saved in `benchmarks\llm-<date>.md`. Look for **PASS** (first word ≤ 1.5 s,
≥ 12 tokens/s, 100 % on the GPU, no empty replies), then read the Tamil and Tanglish answers.

Speed tips:
- Quit Ollama from the tray and start it again once, so `OLLAMA_FLASH_ATTENTION=1` and
  `OLLAMA_KV_CACHE_TYPE=q8_0` take effect (setup saved them; Ollama only reads them at start).
- Close games and other GPU-heavy apps; `ollama ps` should show `100% GPU`.
- Keep `AIRA_LLM_NUM_CTX=4096`. Larger values use more VRAM and can push the model onto the CPU.
- Plug the laptop in and use the Omen *Performance* mode; on battery the GPU slows down.
- If a question really needs careful reasoning, `AIRA_LLM_THINK=true` with
  `AIRA_LLM_MAX_TOKENS=1200` gives better answers but slower ones. It is not suited to voice.
- The model stays loaded in the GPU while AIRA runs (`AIRA_LLM_KEEP_ALIVE=-1`), so no reply waits
  for a reload; AIRA frees the GPU when you stop the server.

### Online AI instead of (or as a backup for) Ollama

With an API key from an AI service, AIRA can think online: no GPU needed, or as a backup when
Ollama is not running. In `.env`:
```
AIRA_LLM_PROVIDER=auto              # auto = Ollama first, online when Ollama is off; online = always online
AIRA_ONLINE_SERVICE=gemini          # openai, gemini, groq, openrouter, deepseek, mistral, together, anthropic
AIRA_ONLINE_MODEL=<model name from the service's model list>
AIRA_ONLINE_API_KEY=<your key>
```
Restart the server; its window says `Online AI ready` (or, in auto mode, which one answers).
What you say is then sent to that company. Laptop skills keep working with models that support
tool calling. The Raspberry Pi can also use an online AI by itself: raspberry_pi/README.md section 6.

## 2c. Task automation: AIRA does things on the laptop

AIRA can act on the laptop, not just chat. Ask in English, Tamil or Tanglish:

| Ask | What happens |
|---|---|
| "open chrome", "VS Code open pannu", "close spotify" | opens / closes apps (closing asks you first) |
| "open youtube", "search for ESP32 OTA tutorials" | opens a site or a web search in your browser |
| "how's my laptop doing?", "is the GPU hot?", "battery evvalavu?" | live CPU, RAM, GPU temperature, battery, disk, internet |
| "volume up", "சத்தம் குறை", "set volume to 40", "mute", "pause the music", "next song" | sound and media keys (Spotify, YouTube, any player) |
| "brightness down", "brightness 70" | screen brightness |
| "set a timer for 10 minutes", "remind me to call Manoj at 6:30 pm", "what reminders do I have?" | timers and reminders (spoken and shown on the Pi; survive a restart) |
| "lock the laptop", "shut down", "restart", "sleep", "cancel shutdown" | power (everything except lock asks "Say yes to confirm") |

**Instant commands.** Short, clear commands (the first column above) skip the AI model
entirely and act in well under a second; the reply shows *instant command*. Anything
else goes to the model, which picks the right skill itself.

**Safety.**
- AIRA can only open apps on a fixed list and websites with a normal address.
- She never runs arbitrary commands, and every argument is checked before anything runs.
- Shut down, restart, sleep and closing apps need your "yes" (spoken, typed, or the
  *Yes, do it* button). Shutdown waits 30 seconds, so "cancel shutdown" still works.

**Your own apps and sites.** Copy `config/apps.example.json` to `config/apps.json`, add
entries, restart. Example: OBS, or `"sodak": "https://sodakedutech.in"` so that "open sodak" works.

On a non-Windows machine, or with `AIRA_AGENT_DRY_RUN=true`, actions are only written to
the log; useful for testing. `AIRA_AGENT_ENABLED=false` turns task automation off.

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

### 3b. AIRA's own voice: clear and fast (Azure, with offline Piper as backup)

Set up **both** if you can. AIRA uses Azure's natural voices whenever they work, and switches
to the offline Piper voices automatically when there is no internet, Azure is slow (over
4 s), or no key is set. You never get silence.

1. **Offline voices (Piper, no account, runs on the CPU)**:
   ```powershell
   pip install piper-tts
   python scripts\download_voices.py
   ```
   This downloads a Tamil female voice (AI4Bharat Rasa, CC-BY-4.0) and a clear English
   voice into `voices\`. AIRA finds them automatically. Mixed sentences are split: Tamil
   words in the Tamil voice, English words in the English one. Use `--tamil male` for the
   male Tamil voice.
2. **Azure (clearest Tamil, needs internet; the free tier is enough for personal use)**:
   create a *Speech* resource in the Azure portal and put its key and region in `.env`:
   ```
   AIRA_AZURE_SPEECH_KEY=<key 1>
   AIRA_AZURE_SPEECH_REGION=centralindia
   ```
   Voices: `ta-IN-PallaviNeural` (Tamil/Tanglish) and `en-IN-NeerjaNeural` (English).

Restart the server. The window shows `speech output on azure+piper`. Speed and clarity:
- `AIRA_TTS_RATE=1.1` is the speaking speed; 1.0 is calmer and clearer, 1.2 faster.
- Short repeated phrases ("Opening Chrome.") are cached and replay instantly.
- AIRA starts speaking after the first sentence (or the first comma of a long one), while
  the rest is still being written.

Settings → *AIRA's voice* lets each browser choose the laptop voice, the browser voice, or
text only. If `piper-tts` fails to install on Python 3.14, use Python 3.12 (see 3a).

#### Voice style: sweet young girl

Add one line to `.env` and restart the server:
```
AIRA_TTS_VOICE_STYLE=girl
```

What changes, for each voice path:

| Voice | Effect |
|---|---|
| Azure | English uses `en-US-AnaNeural`, Azure's child voice. Tamil uses Pallavi with a higher pitch (+18%) and a slightly brisker pace |
| Offline Piper | both voices shifted up 3.5 semitones (about 260 Hz, a 12-year-old girl's range), same speed. Measured with Whisper: English 0% word errors; Tamil about 20% character errors, mostly the Tamil voice model itself |
| Browser | only girl / woman voices are used; a man's voice (Windows: David, Ravi, Valluvar) never. If the browser has no girl voice for a language (Chrome has no female Tamil voice), that sentence uses the laptop's Piper girl voice. In **Microsoft Edge** English uses its child voice "Ana", the most natural option without Azure |

The hint under the text box says which voice is speaking (e.g. `voice: piper on the laptop (girl)`
or `voice: browser: Ana`). If AIRA sounds like a man, open Settings and set **AIRA's voice** to
**Auto** or **Server**. Younger still: `AIRA_TTS_GIRL_PIPER_SEMITONES=4.5` (or 5.5) in `.env`.

Before speaking, the text is cleaned so symbols are said as words: no "asterisk", emoji names or
web addresses read aloud; `24°C` becomes "24 degrees Celsius", `&` "and", `e.g.` "for example".
For clearly better Tamil than the offline voice, add an Azure key (free tier, Tamil voice Pallavi).

Hear the styles side by side (needs Azure or Piper set up): `python scripts\voice_preview.py`
(add `--pitch 12 18 24` to compare Tamil pitch levels). Fine-tune with
`AIRA_TTS_GIRL_TA_PITCH_PCT`, `AIRA_TTS_GIRL_EN_PITCH_PCT`, `AIRA_TTS_GIRL_RATE` and
`AIRA_TTS_GIRL_PIPER_SEMITONES`. `AIRA_TTS_VOICE_STYLE=default` goes back to the adult voices.

## 4. Use it from your phone (or another device)

Browsers only allow the microphone on **https://** pages (or `localhost`). Plain
`http://<laptop-ip>` will show the page, but the microphone stays blocked. So:

**a. Same network.** Connect the laptop and the phone to the same Wi-Fi, or connect the
laptop to the phone's hotspot. On the laptop: Settings → Network & internet → Wi-Fi → this
network → **Network profile type: Private**.

**b. Make an HTTPS certificate** (once; the phone accepts it on any address. Re-running it
changes the certificate, and then the Raspberry Pi must be paired again):

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

**f. Raspberry Pi.** The same settings (LAN + HTTPS) let AIRA's Pi connect. The server
announces itself on the network (UDP 8766, outgoing only) so the Pi finds it even when the
hotspot gives the laptop a new address. Setup on the Pi: [raspberry_pi/README.md](raspberry_pi/README.md).
`.\scripts\connect_pi.ps1` checks all of this from Windows (`-Fix` as Administrator repairs it,
`-Deploy` copies the Pi folder, `-Pair` pairs over SSH).

## 5. Using the interface

The page uses AIRA's neo-brutalist design (black strokes, teal / magenta / yellow on paper) with her pixel-art
mascot, the same sprites as the Raspberry Pi screen. She listens, thinks and talks (moving mouth),
blinks, smiles after a task, gets excited for reminders and sleepy after 3 quiet minutes.

| Control | What it does |
|---|---|
| Microphone (teal) / space bar | Start listening; tap again to stop early. While AIRA speaks, it interrupts her |
| Quick actions (wide screens, under AIRA) | One tap for laptop status, volume, play/pause, a 5-minute timer, reminders, lock |
| Text box + Enter | Type instead of speaking |
| Circular arrow (top right) | New conversation (AIRA forgets the current one) |
| Sliders (top right): Settings | Speech recognition (Auto / Server / Browser), AIRA's voice (Auto / Server / Browser / Off), language, auto-stop, "keep listening after AIRA replies" for hands-free conversation, browser voice speed, *Forget this device* |
| **Pi screen on / off** (top bar) | Whether AIRA's Raspberry Pi screen is connected. Everything you say or type here also shows on the Pi: her face, listening / thinking / speaking, the captions |
| AIRA's screen (Pi) buttons (wide screens) | Show Home, Chat, Music, Tools, Emotions (plays through her emotions), About or System on the Pi for a minute (no touch needed) |
| Commands from the Pi's touch screen | Appear here marked "on AIRA Pi", and this page speaks AIRA's answer (the Pi has no speaker yet) |
| AIRA's card | The label shows her state: Ready, Listening (teal), Thinking (yellow), Speaking, Error (magenta) |

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
| Replies take 10 s+ or come back empty (`ttft=None` in the log) | The model is "thinking" silently: keep `AIRA_LLM_THINK=false` (default) and see section 2b |
| Replies slow even with thinking off | `ollama ps`: if it is not `100% GPU`, use a smaller model (2b) or close other GPU apps |
| "open X" says it can't open it | Only listed apps open: add it to `config/apps.json` (section 2c) |
| Brightness doesn't change | Only the laptop's own screen supports it (not external monitors) |
| Server voice stops, browser voice takes over | Azure failed and no Piper voices are installed: run `scripts\download_voices.py` |
| Pi shows "looking for the laptop" | Server needs `AIRA_ALLOW_LAN=true`, same hotspot, firewall rule (4d); see raspberry_pi/README.md |
| Status "AI model not ready" | Ollama not running or model missing: `ollama list`, then check the server window |
| Phone can't open the page | Different networks, network profile is *Public*, or firewall (section 4a/4d); check the address in the server window |
| Certificate error even after "Proceed" | The laptop's address changed: run `python scripts\make_dev_cert.py` again and restart |
| Wrong pairing code / "pairing locked" | The code changes after every successful pairing; after 5 wrong tries restart the server |
| Speech recognition misses Tamil | Settings → *I'll speak* → *Tamil / Tanglish*; use Whisper (3a) rather than browser mode |
| AIRA's voice sounds robotic or English-accented in Tamil | Set up Azure (3b), or pick another Tamil voice in the phone's text-to-speech settings |
| First spoken reply takes long | Whisper is loading; the next ones are fast. AI model warm-up also happens at server start |
| No sound | Phone media volume / silent mode; Settings → *AIRA's voice* not *Off* |
| AIRA sounds like a man | The browser voice is in use: Settings → *AIRA's voice* → *Auto*. The hint under the text box names the voice |
| "Online AI refused the API key" / "model not found" | Check `AIRA_ONLINE_API_KEY` / `AIRA_ONLINE_MODEL` (exact name from the service) |

## 8. Security and privacy

- The server listens only on the laptop (`localhost`) unless you set `AIRA_HOST=0.0.0.0` and `AIRA_ALLOW_LAN=true`. Never forward port 8765 on a router.
- Every browser must pair with the code shown in the server window (someone must be at the laptop). Codes work once and lock after 5 wrong tries.
- Paired browsers are stored as hashes in `data/web_devices.json`. Delete that file (with the server stopped) to unpair all browsers. On a phone, use Settings → *Forget this device*.
- Audio you upload is processed in memory and a temporary file, then deleted. Conversation text is not written to logs unless `AIRA_LOG_CONVERSATION_CONTENT=true`.
- `data/`, `certs/` and `.env` are git-ignored: never commit them.
- With `AIRA_LLM_PROVIDER=online` or `auto`, what you say (and tool results such as your laptop's status) goes to the online AI service you chose. `ollama` keeps everything on the laptop.

## 9. Stop

Press **Ctrl+C** in the server window.

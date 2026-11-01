# AIRA configuration

AIRA has two settings files:

| Where | File | How to change it |
|---|---|---|
| Laptop (the brain) | `.env` in the project folder (template: `.env.example`) | edit, then restart the server (`.\scripts\run_server.ps1`) |
| Raspberry Pi (the face) | `~/.config/aira-pi/config.json` on AIRA (readable only by `aira`) | the `python3 -m aira_pi ...` commands below, then `sudo systemctl restart aira-pi` |

Secrets (API keys, the Pi's pairing token) live only in these two files. They are never
logged, shown on screen or put in documentation.

## Ways to run AIRA

| | AI brain | You talk | AIRA speaks | Needs |
|---|---|---|---|---|
| **Laptop, local** (default) | Ollama on the laptop | web page (laptop / phone) | laptop speakers (or phone) | Ollama + model |
| **Laptop, online** | online AI (your API key) | web page | laptop speakers (or phone) | internet + API key; no GPU |
| **Laptop, auto** | local; online when Ollama is off | web page | laptop speakers | both of the above |
| **Pi + laptop** | the laptop (any of the three above) | web page, Pi touch screen, Pi chat page | web page / laptop | paired Pi |
| **Pi alone** | online AI from the Pi | Pi chat page (phone) | the phone's browser | API key on the Pi; internet |

Editions of the hardware: **AIRA Half** (now) is the Pi with only its screen; you talk on a
web page. **AIRA Full** adds the I²S microphone, speaker and battery, so you talk to the Pi itself.

## Laptop `.env`

### 1. Security and network
| Setting | Default | Meaning |
|---|---|---|
| `AIRA_API_KEY` | (empty) | 32+ random characters; `python scripts/generate_api_key.py` |
| `AIRA_HOST` | `127.0.0.1` | `0.0.0.0` lets the phone and the Pi connect (also set `AIRA_ALLOW_LAN=true`) |
| `AIRA_ALLOW_LAN` | `false` | must be `true` for anything but `127.0.0.1` |
| `AIRA_SSL_CERTFILE`, `AIRA_SSL_KEYFILE` | — | HTTPS (`python scripts\make_dev_cert.py`); needed for phone microphones and the Pi's pinned link |
| `AIRA_PORT` | `8765` | |

`.\scripts\connect_pi.ps1` checks these, the certificate, the firewall and the network type.

### 2. AI brain
| Setting | Default | Meaning |
|---|---|---|
| `AIRA_LLM_PROVIDER` | `ollama` | `ollama`, `online` or `auto` (local first, online when Ollama is unreachable) |
| `AIRA_LLM_MODEL` | `qwen3.5:9b` in the template | the Ollama model |
| `AIRA_LLM_KEEP_ALIVE` | `-1` | keep the model in the GPU while AIRA runs (no reload delay); freed when AIRA stops |
| `AIRA_LLM_THINK` | `false` | hidden reasoning before replies (slow); keep `false` for voice |
| `AIRA_LLM_MAX_TOKENS`, `AIRA_LLM_NUM_CTX`, `AIRA_LLM_TEMPERATURE` | 400, 4096, 0.7 | reply length, memory window, creativity (used by online too) |
| `AIRA_ONLINE_SERVICE` | — | `openai`, `gemini`, `groq`, `openrouter`, `deepseek`, `mistral`, `together`, `anthropic` |
| `AIRA_ONLINE_BASE_URL` | — | instead of a service: any OpenAI-compatible URL (e.g. LM Studio `http://localhost:1234/v1`) |
| `AIRA_ONLINE_MODEL` | — | the model name exactly as the service lists it |
| `AIRA_ONLINE_API_KEY` | — | your key / token |

Online AI sends what you say (and tool results such as your laptop's status) to that
company. Task automation (opening apps, volume, timers…) works with online models that
support tool calling; actions still run only on your laptop and risky ones still ask first.

### 3. Personality
`AIRA_USER_NAME`, `AIRA_TAMIL_OUTPUT_SCRIPT` (`tamil` = Tamil script, best for the voices; `latin` = Tanglish),
`AIRA_PERSONA_FILE`.

### 4. Voice
| Setting | Default | Meaning |
|---|---|---|
| `AIRA_TTS_VOICE_STYLE` | `girl` in the template | `girl` = sweet young girl (about 12); `default` = adult voices |
| `AIRA_TTS_PROVIDER` | `auto` | Azure if a key is set, offline Piper as backup; or `piper`, `azure`, `none` |
| `AIRA_TTS_GIRL_PIPER_SEMITONES` | `3.5` | offline girl voice pitch (3.5 = voice A; 4.5 / 5.5 younger) |
| `AIRA_TTS_GIRL_RATE` | `1.06` | girl voice speed; `1.0` is a little slower |
| `AIRA_TTS_PIPER_PITCH_METHOD` | `wsola` | `wsola` = voice A, keeps the speaking speed; `native` = experimental (Piper slower, then sped up; comes out faster, so less clear) |
| `AIRA_TTS_PIPER_NOISE_SCALE`, `AIRA_TTS_PIPER_NOISE_W` | voice's own | Piper variation; leave unset unless testing |
| `AIRA_AZURE_SPEECH_KEY`, `AIRA_AZURE_SPEECH_REGION` | — | optional Azure neural voices (child voice "Ana" for English) |
| `AIRA_STT_PROVIDER`, `AIRA_STT_MODEL` | `auto`, `large-v3-turbo` | speech recognition on the laptop (else the browser) |

Before speaking, AIRA's text is cleaned: no markdown, emoji or links; `24°C` becomes
"24 degrees Celsius", `&` "and", `e.g.` "for example". The web page shows which voice is speaking.

### 5. Task automation
`AIRA_AGENT_ENABLED`, `AIRA_AGENT_DRY_RUN`, `AIRA_AGENT_FAST_COMMANDS`, `AIRA_AGENT_APPS_FILE` (HOW-TO-RUN.md 2c).

### 6. Devices
`AIRA_DEVICE_DISCOVERY` (UDP beacon so the Pi finds the laptop on a hotspot).

## Raspberry Pi `config.json`

| Key | Default | Set with | Meaning |
|---|---|---|---|
| `edition` | `half` | edit | `half` (screen only) or `full` (mic, speaker, battery) |
| `brain` | `laptop` | `python3 -m aira_pi online setup` | `laptop`, `online`, or `auto` (laptop when connected, else online) |
| `online_service`, `online_base_url`, `online_model`, `online_api_key` | — | `online setup` | the Pi's own online AI (same services as the laptop) |
| `chat_page`, `chat_port`, `chat_code` | on, 8770, made at first start | edit | the chat page `http://<pi-ip>:8770/`; the code is on the System screen |
| `laptop_url`, `token`, `cert_pem`, `fingerprint` | — | `python3 -m aira_pi pair` | the pairing with the laptop (secret) |
| `touch` | `auto` | edit / `touch_spi.sh` | `auto` (direct SPI reader if enabled, else kernel), `evdev`, `spi`, `off` |
| `touch_cal`, `touch_spi_threshold` | —, 400 | `python3 -m aira_pi calibrate` | touch alignment; press sensitivity of the SPI reader |
| `controller`, `controller_port`, `controller_token` | on, 8771, made at first start | `python3 -m aira_pi controller` | the ESP32 joystick link; the token goes into the ESP32's `secrets.h` (`--new-token` replaces it) |
| `theme` | `auto` | edit | `auto`/`brutal` (touch menu), `mascot`, `simple` |
| `rotate`, `fps`, `button_gpio`, `audio`, `framebuffer` | 0, 8, 26, auto, auto | edit | screen and hardware |

`python3 -m aira_pi info` and `python3 -m aira_pi online status` show the settings without secrets.

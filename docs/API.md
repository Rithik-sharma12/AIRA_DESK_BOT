# AIRA laptop server API — v1 (Phase 1)

Base URL: `http://127.0.0.1:8765/api/v1` · Interactive docs: `http://127.0.0.1:8765/docs`

Authentication: every endpoint except `/health` needs
`Authorization: Bearer <AIRA_API_KEY>` (or `X-API-Key: <key>`). Wrong or missing key → `401`.

## Endpoints

### `GET /health` (public)
`{"status": "ok", "version": "0.1.0"}` — liveness only, reveals nothing else.

### `GET /status`
Model, Ollama version, installed models, loaded models with `% in VRAM`, active sessions.
`status` is `degraded` when Ollama is unreachable or the model is missing.

### `POST /chat`
Request:
```json
{"message": "Enna AIRA, innikku enna plan?", "session_id": "optional-id"}
```
`message`: 1–4000 chars (configurable). `session_id`: `[A-Za-z0-9_-]{1,64}`; omit to start a
new conversation — the response returns the id to reuse. Unknown fields are rejected.

Response:
```json
{
  "session_id": "c794a7be...",
  "reply": "இன்னைக்கு Java study பண்ணலாமா?",
  "stats": {"model": "gemma4:e4b-it-qat", "ttft_s": 0.41, "tokens_per_s": 38.2,
            "load_s": 0.0, "prompt_tokens": 512, "completion_tokens": 24, "...": "..."}
}
```

### `POST /chat/stream`
Same request; response is `text/event-stream`:

```
event: start   data: {"session_id": "..."}
event: token   data: {"text": "இன்னைக்கு"}          (repeated)
event: tool    data: {"name": "open_app", "ok": true, "say": "Opening Chrome.", "fast": true}
event: confirm data: {"tool": "power", "prompt": "Do you want me to shut down the laptop? Say yes to confirm."}
event: done    data: {"session_id": "...", "reply": "...", "stats": {...}}
event: error   data: {"code": "llm_unavailable", "message": "...", "hint": "..."}
```

Phase 4 (voice) consumes this stream sentence by sentence so TTS starts before the
reply is finished. `tool` appears when AIRA used a laptop skill (task automation);
`confirm` means AIRA is waiting for "yes"/"no" in the next message before a risky action.
Instant commands report `stats.model = "aira-fast"`.

### Live events and devices
| Method | Path | |
|---|---|---|
| GET | `/events` | SSE stream of hub events: `state`, `caption` (with `source`: `web` or the device name), `reminder`, `confirm`, `wake`, `screen` |
| POST | `/events/state` | `{"state": "listening"\|"thinking"\|"speaking"\|"idle"\|"error"}` from the web page |
| GET | `/devices` | connected Raspberry Pi devices |
| POST | `/devices/screen` | `{"screen": "home"\|"chat"\|"music"\|"tools"\|"system"}`: show that screen on the Pi for a minute |
| WS | `/device/ws` | the device link (below); `Authorization: Bearer <device token>` |

### `DELETE /sessions/{session_id}`
Forget a conversation. `{"session_id": "...", "deleted": true}`

### `GET /metrics`
Rolling window of recent requests: TTFT p50/p95 and tokens/s over *warm* requests,
max load time, error counts by code.

## Web voice interface (prototype)

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /` | public | the web page |
| `POST /api/v1/web/hello` | public | prints the pairing code in the server console; returns `{"locked": bool}` |
| `POST /api/v1/web/pair` `{"code","name"}` | public | returns `{"token"}` for this browser; 403 on a wrong code or when locked |
| `GET /api/v1/voice/capabilities` | key or web token | `{"stt": "faster-whisper"|null, "tts": "azure"|"piper"|null, ...}` |
| `POST /api/v1/voice/transcribe` (multipart `audio`, `language`=auto/en/ta) | key or web token | `{"text","language","audio_s","latency_s"}`; 501 if no server STT |
| `POST /api/v1/voice/speak` `{"text"}` | key or web token | `audio/wav`; 501 if no server TTS |

Web tokens are sent like the API key (`Authorization: Bearer <token>`) and work on all
authenticated endpoints. See HOW-TO-RUN.md.

## Error format

```json
{"code": "llm_model_not_found", "message": "Model 'x' is not installed in Ollama.", "hint": "Run:  ollama pull x"}
```

| HTTP | code | Meaning |
|---|---|---|
| 401 | — | Missing/invalid API key |
| 413 | — | Message too long |
| 422 | — | Validation error (empty message, bad session id, unknown field) |
| 502 | `llm_error` | Ollama returned an error |
| 503 | `llm_unavailable` / `llm_model_not_found` | Ollama down / model not pulled |
| 504 | `llm_timeout` | No response within `AIRA_LLM_REQUEST_TIMEOUT_S` |

## Device protocol (served at `/api/v1/device/ws`)

`shared/protocol/messages.py` defines the versioned envelope used on the WebSocket link:

```json
{"v": "1.1", "type": "DISPLAY_STATE_CHANGED", "id": "…", "ts": "2026-10-30T12:00:00Z",
 "device_id": "pi-zero-01", "session_id": null, "reply_to": null,
 "payload": {"expression": "happy", "intensity": 1.0}}
```

Types: DEVICE_HELLO, SERVER_WELCOME, DEVICE_HEARTBEAT, WAKE_WORD_DETECTED, LISTENING_STARTED,
LISTENING_STOPPED, AUDIO_CHUNK, TRANSCRIPTION_READY, THINKING_STARTED, SPEECH_STARTED,
SPEECH_FINISHED, BARGE_IN, DISPLAY_STATE_CHANGED, SYSTEM_STATUS, BUTTON_EVENT, TOUCH_EVENT,
USER_TEXT, CAPTION, SAY, REMINDER, CONFIRM_REQUEST, ERROR; 1.2 adds SHOW_SCREEN.
Unknown types and unknown envelope fields are rejected.

Link: the Pi connects, sends `DEVICE_HELLO {name, version, caps}`, receives `SERVER_WELCOME`,
then follows `DISPLAY_STATE_CHANGED` / `CAPTION` / `REMINDER` / `CONFIRM_REQUEST`. It sends
`BUTTON_EVENT` (wakes the web page's microphone), `USER_TEXT` (runs the agent; a Pi with
audio then receives one `SAY` per sentence) and `DEVICE_HEARTBEAT`.
Discovery: UDP broadcast on port 8766, `{"service":"aira","port":8765,"tls":true,"fp":"<sha256 of the cert>"}`.

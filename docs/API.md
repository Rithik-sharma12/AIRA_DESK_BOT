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
event: done    data: {"session_id": "...", "reply": "...", "stats": {...}}
event: error   data: {"code": "llm_unavailable", "message": "...", "hint": "..."}
```

Phase 4 (voice) consumes this stream sentence by sentence so TTS starts before the
reply is finished.

### `DELETE /sessions/{session_id}`
Forget a conversation. `{"session_id": "...", "deleted": true}`

### `GET /metrics`
Rolling window of recent requests: TTFT p50/p95 and tokens/s over *warm* requests,
max load time, error counts by code.

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

## Device protocol (Phase 6 transport, defined now)

`shared/protocol/messages.py` defines the versioned envelope used on the WebSocket link:

```json
{"v": "1.0", "type": "DISPLAY_STATE_CHANGED", "id": "…", "ts": "2026-10-30T12:00:00Z",
 "device_id": "pi-zero-01", "session_id": null, "reply_to": null,
 "payload": {"expression": "happy", "intensity": 1.0}}
```

Types: DEVICE_HELLO, SERVER_WELCOME, DEVICE_HEARTBEAT, WAKE_WORD_DETECTED, LISTENING_STARTED,
LISTENING_STOPPED, AUDIO_CHUNK, TRANSCRIPTION_READY, THINKING_STARTED, SPEECH_STARTED,
SPEECH_FINISHED, BARGE_IN, DISPLAY_STATE_CHANGED, SYSTEM_STATUS, BUTTON_EVENT, TOUCH_EVENT, ERROR.
Unknown types and unknown envelope fields are rejected.

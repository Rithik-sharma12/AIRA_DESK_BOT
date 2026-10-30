# Phase 1 acceptance — AI brain

Phase 1 is done when every row below is checked **on the HP Omen**. Record the evidence
(command output, benchmark file name) next to each item. Do not start Phase 2 before then.

## Verified in the build environment (no GPU, Ollama emulator)

- [x] 39 unit tests pass (config, persona, conversation trimming/expiry, auth, API, SSE, Ollama client parsing and error mapping, protocol, metrics)
- [x] 4 end-to-end tests pass: real server process + Ollama emulator over HTTP (startup health check and warm-up, 401 + audit log, Tamil SSE round trip, content not logged)
- [x] All scripts run: setup key generation, environment check, benchmark, CLI chat

These prove the plumbing. They say nothing about model quality or speed on your laptop.

## To verify on the HP Omen

| # | Criterion | How | Evidence |
|---|---|---|---|
| 1 | Environment clean | `python scripts\check_environment.py` → 0 FAIL | |
| 2 | Tests pass on Windows | `python -m pytest` | |
| 3 | Exact model tags verified | `ollama list` shows the tags used | |
| 4 | Benchmark report exists for ≥ 2 models | `benchmarks\llm-*.md` | |
| 5 | Chosen model 100% in VRAM at chosen context | benchmark "% in VRAM" column, or `ollama ps` | |
| 6 | Warm TTFT p50 ≤ 1.5 s | benchmark | |
| 7 | Generation ≥ 12 tokens/s (min across runs) | benchmark | |
| 8 | Cold load time recorded | benchmark "cold load s" | |
| 9 | English, Tamil and Tanglish replies are understandable and in the right language/script | live tests (`AIRA_LIVE_TESTS=1`) + your judgement | |
| 10 | AIRA does not invent laptop readings ("What is my CPU temperature?") | live test `honesty` | |
| 11 | Conversation context works (asks back about an earlier message) | `chat_cli.py` | |
| 12 | Unauthenticated request rejected | `curl http://127.0.0.1:8765/api/v1/status` → 401 | |
| 13 | Server not reachable from another device on the Wi-Fi | from phone: `http://<laptop-ip>:8765/api/v1/health` fails | |
| 14 | Recovers when Ollama restarts | quit Ollama → request gives `llm_unavailable` → start Ollama → next request works | |
| 15 | Model choice recorded | `AIRA_LLM_MODEL` set in `.env`, decision noted in DESIGN.md §F | |

The 1.5 s / 12 tok/s targets are starting points: a person speaks about 3–4 tokens' worth
per second, so 12 tok/s keeps a streaming TTS comfortably fed. Tighten them once Phase 2
measures real STT and TTS latency.

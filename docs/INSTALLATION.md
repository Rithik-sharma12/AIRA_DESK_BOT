# Installation — Phase 1 (HP Omen 16, Windows 11)

## 1. Prerequisites

| Item | How | Check |
|---|---|---|
| NVIDIA driver (recent Game Ready or Studio) | GeForce Experience / nvidia.com | `nvidia-smi` shows the RTX 4060 Laptop GPU |
| Python 3.12 (3.11 also fine) | python.org installer, tick "Add to PATH" | `py -3.12 --version` |
| Ollama for Windows | `winget install Ollama.Ollama` or ollama.com/download | `ollama --version` |
| Git (optional) | `winget install Git.Git` | `git --version` |

Laptop power: plug in the charger and choose the *Performance* mode in OMEN Gaming Hub;
on battery the GPU is power-limited and benchmarks will be misleading.

Optional but recommended: set the NVIDIA Control Panel / Windows Graphics settings so the
desktop runs on the integrated GPU where possible (frees VRAM on the RTX 4060).

## 2. Ollama memory settings

`scripts\setup_windows.ps1` sets these as user environment variables; you can also set
them by hand (Settings → System → About → Advanced system settings → Environment Variables):

| Variable | Value | Why |
|---|---|---|
| `OLLAMA_FLASH_ATTENTION` | `1` | Lower memory for attention |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | Halves KV-cache memory vs f16 with minimal quality loss (needs flash attention) |
| `OLLAMA_HOST` | *leave unset* | Default is localhost only. Never `0.0.0.0` — Ollama has no authentication |

Quit Ollama from the system tray and start it again after changing them.

## 3. Models

```powershell
ollama pull gemma4:e4b-it-qat     # ~6 GB download, expected to fit fully in 8 GB VRAM
ollama pull gemma4:12b-it-qat     # ~7 GB; expected to spill partly to CPU on 8 GB — benchmark decides
ollama pull gemma4:e2b-it-qat     # optional fallback
ollama list
```

If a tag is not found, list the real tags at https://ollama.com/library/gemma4 and use
those in `.env` (`AIRA_LLM_MODEL`) and on the benchmark command line (`--models`).

## 4. AIRA server

```powershell
cd path\to\AIRA
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows.ps1
.\.venv\Scripts\Activate.ps1
python scripts\check_environment.py
python -m pytest
```

Edit `.env` to set `AIRA_USER_NAME` (how AIRA addresses you) and, after benchmarking,
`AIRA_LLM_MODEL`.

## 5. Benchmark

Close games, video and GPU-accelerated browsers first.

```powershell
python scripts\benchmark_llm.py --models gemma4:e4b-it-qat gemma4:12b-it-qat --ctx 4096 8192 --runs 3
python scripts\benchmark_llm.py --models gemma4:e4b-it-qat --think off    # if replies are slow to start
```

Report: `benchmarks\llm-<timestamp>.md` (+ raw JSON). Pick the model that PASSes with the
best answers in the "Sample replies" section, then set `AIRA_LLM_MODEL`.

## 6. Run and talk

```powershell
.\scripts\run_server.ps1
# second window
.\.venv\Scripts\Activate.ps1; chcp 65001
python scripts\chat_cli.py
```

Use Windows Terminal (not the legacy console) so Tamil script renders correctly.

Live model tests (prints timings and replies in English, Tamil, Tanglish):

```powershell
$env:AIRA_LIVE_TESTS = "1"; python -m pytest tests\integration\test_live_ollama.py -v -s
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `llm_unavailable` | Ollama not running — start it from the Start menu; check `AIRA_OLLAMA_BASE_URL` |
| `llm_model_not_found` | `ollama pull <tag>` or fix `AIRA_LLM_MODEL` |
| Slow replies, `% in VRAM` < 100 | Model + context too big for free VRAM: use E4B, lower `AIRA_LLM_NUM_CTX`, close GPU apps |
| First reply takes several seconds | Cold load; normal once. `AIRA_LLM_KEEP_ALIVE=30m` keeps it warm |
| `?????` instead of Tamil | Run `chcp 65001` and use Windows Terminal |
| Configuration error about API key | `python scripts\generate_api_key.py` |

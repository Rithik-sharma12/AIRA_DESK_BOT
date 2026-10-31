# Phase 2 — Voice bake-off (TTS, STT, wake word)

Goal: choose AIRA's speech engines by measurement on your own voice and your own laptop,
before building the voice pipeline (Phase 4). Phase 1's model benchmark should be done
first, because STT shares the RTX 4060 with Gemma.

Everything runs from the repository root with the venv active.

## 0. Install

```powershell
pip install -r requirements-voice.txt
```

| Engine | Extra setup |
|---|---|
| **Azure TTS** | Azure portal → create a *Speech* resource (free tier available; check current limits) in a region such as Central India → *Keys and Endpoint* → put Key 1 and the region in `.env` as `AIRA_AZURE_SPEECH_KEY` and `AIRA_AZURE_SPEECH_REGION`. Replies are sent to Microsoft for synthesis. |
| **Piper (offline, CPU)** | Download `ta_IN-rasa_female-medium.onnx` **and** `ta_IN-rasa_female-medium.onnx.json` from huggingface.co/tinisoft/piper-ta_IN-rasa_female-medium into `models/piper/`. Community model, not an official Piper voice. |
| **Indic Parler-TTS (offline, GPU)** | `pip install git+https://github.com/huggingface/parler-tts.git` plus a CUDA build of PyTorch (pytorch.org). First run downloads several GB. |
| **faster-whisper (STT)** | Installed by `requirements-voice.txt`. GPU needs the CUDA 12 cuBLAS and cuDNN libraries that CTranslate2 expects (faster-whisper README, GPU section). Until then use `--device cpu --compute-type int8`. |

Engines whose dependencies or keys are missing are skipped with a reason, so you can start
with Azure alone.

## 1. TTS: synthesise, rate blind, score

```powershell
python scripts\bakeoff\run_tts.py --sentences en01 ta01 tg01     # 1-minute smoke test
python scripts\bakeoff\run_tts.py                                 # full: 40 sentences x every engine
```

The run folder `bakeoff_runs\tts-<time>\` contains `listen.html`. Send the folder (zip it)
to at least three Tamil speakers. Each opens `listen.html`, rates every clip 1–5 on four
criteria, and clicks *Download my ratings*. Clips are shuffled under blind ids, so raters
cannot tell which engine made which clip.

Put every `ratings_<name>.csv` back into the run folder, then:

```powershell
python scripts\bakeoff\score_tts.py bakeoff_runs\tts-<time>
```

`report.md` ranks engines and applies the rule: **highest mean rating among engines whose
median time to first audio is under 800 ms**. Engine settings (voices, rate, pitch,
strategy, Azure styles, the Parler voice description) live in
`scripts\bakeoff\tts_engines.json`; add variants there and re-run.

Three voicing strategies are compared for Azure:

| Strategy | What it does |
|---|---|
| `ta_only` | Whole sentence in the Tamil voice — one consistent timbre, English with a Tamil Nadu accent |
| `split` | Tamil words → Tamil voice, English words → Indian English voice (cheerful style) |
| `auto` | English-only sentences → English voice; anything with Tamil → Tamil voice |

## 2. STT: record yourself, transcribe, score

```powershell
python scripts\bakeoff\record_stt.py --list-devices
python scripts\bakeoff\record_stt.py --device "Microphone"        # name substring or index
python scripts\bakeoff\run_stt.py --with-llm                       # Gemma loaded, as in real use
python scripts\bakeoff\run_stt.py --with-llm --prompt              # same, with a vocabulary prompt
```

AIRA's real microphone is the INMP441 on the Pi, which is not wired yet. Record now with the laptop's microphone at about the distance you will sit from AIRA, speaking normally; once the INMP441 works (HARDWARE.md §6.3), record a second set on the Pi and re-run `run_stt.py` on it, because a MEMS mic in a small case sounds different. The 50
prompts are the 40 TTS sentences plus 10 typical commands. Recordings stay in
`bakeoff_recordings\` (git-ignored).

Read `transcripts.csv`, not just the numbers. A common failure is Whisper writing English
words in Tamil script (or the reverse), which inflates the error rate even when the meaning
was caught. If that dominates, Phase 4 can normalise scripts before scoring and before the
LLM sees the text.

## 3. Wake word ("Hey AIRA")

1. Train a custom openWakeWord model with the project's training notebook (synthetic speech; no recordings needed).
2. Add 20–50 of your own "Hey AIRA" recordings and some negative audio (TV, conversation) to train a verifier model if false triggers are high.
3. Measure on the laptop now and again on the Pi in Phase 5: detection rate over 50 attempts at 1 m and 2 m, and false triggers over 8 hours of normal room noise.
4. If openWakeWord misses the targets, try Porcupine (free personal tier; train in the Picovoice console).

## Phase 2 acceptance

| # | Criterion | Evidence |
|---|---|---|
| 1 | TTS report from ≥ 3 raters exists | `bakeoff_runs\tts-*\report.md` |
| 2 | Chosen TTS has median first audio < 800 ms | same report |
| 3 | An offline TTS fallback chosen (best-rated offline engine) | same report |
| 4 | STT report on your 50 recordings with the LLM loaded | `bakeoff_runs\stt-*\report.md` |
| 5 | Chosen STT engine: Tanglish CER ≤ 15% | same report |
| 6 | Wake word: ≥ 95% detection at 2 m, ≤ 1 false trigger per 8 h | your test log |
| 7 | Decisions recorded in the design doc (section E) and `.env` | |

If no STT engine reaches 15% CER on Tanglish, do not lower the bar silently: record the
best result, and decide between fine-tuning Whisper on your recordings, a Tamil-specific
model for Tamil-only turns, or accepting a typed fallback for hard cases.

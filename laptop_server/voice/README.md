# voice/

| Module | Purpose | Phase |
|---|---|---|
| `text.py` | Sentence splitting, streaming `SentenceStream`, Tamil/English script runs | P2 ✓ (used by P4) |
| `audio.py` | `AudioClip` (PCM16 + WAV I/O), concatenation | P2 ✓ |
| `metrics.py` | CER / WER with Tamil-safe normalisation | P2 ✓ |
| `tts/` | `TTSProvider` interface; Azure (REST + SSML strategies), Piper, Indic Parler-TTS | P2 ✓ |
| `stt/` | `STTProvider` interface; faster-whisper | P2 ✓ |
| VAD, sentence-streamed TTS service, barge-in | Live voice pipeline | P4 |

Engines are chosen by the Phase 2 bake-off: see `docs/PHASE2_VOICE_BAKEOFF.md`.

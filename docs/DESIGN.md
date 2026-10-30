# AIRA — Engineering design (summary)

The full design package (deliverables A–J: proposal, architecture diagram, hardware and
software architecture, technology stack, Tamil-English voice comparison, model
feasibility, repository structure, roadmap with acceptance gates, threat model, shopping
list, risks and sources) is maintained as a living document:

**https://claude.ai/artifact/Wfb2XQZwTeiUmtRhkxW3St**

Export it to Markdown, Word or PDF from that page to keep a copy in this folder.

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Where AI runs | All on the HP Omen; the Pi only captures, displays, plays | 512 MB RAM on the Pi Zero 2 W |
| Default model | `gemma4:e4b-it-qat` (6.1 GB) | Fits entirely in 8 GB VRAM; `gemma4:12b-it-qat` (7.2 GB) is benchmarked but likely spills to CPU |
| Phase order | Voice bake-off right after the brain (P2) | Tamil STT/TTS quality is the largest unknown |
| Pi audio | USB speakerphone with echo cancellation via OTG | Zero 2 W has no analog out; AEC enables barge-in |
| Wake word | On the Pi (openWakeWord vs Porcupine in P2) | Audio leaves the Pi only after "Hey AIRA" |
| Tamil text | Tamil words in Tamil script, English in Latin | Lets the TTS router pick the right voice per run |
| Laptop actions | Allowlisted tools, validated parameters, confirmation for sensitive actions, audit log | No arbitrary shell execution, ever |
| Network | Server on 127.0.0.1 until P6; then TLS WebSocket with device pairing | Ollama never leaves localhost |

## Phases

P1 AI brain (built) → P2 voice bake-off → P3 agent engine → P4 voice pipeline →
P5 Raspberry Pi → P6 device link → P7 memory → P8 advanced capabilities → P9 release.
Each phase ends with a measurable acceptance gate (see the design doc, section H, and
`PHASE1_ACCEPTANCE.md`).

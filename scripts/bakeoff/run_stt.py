"""Phase 2 STT bake-off: transcribe your recordings with each engine and score CER/WER.

  python scripts/bakeoff/run_stt.py
  python scripts/bakeoff/run_stt.py --engines large-v3:auto large-v3:ta large-v3-turbo:auto
  python scripts/bakeoff/run_stt.py --with-llm          # Gemma loaded in VRAM, as in real use
  python scripts/bakeoff/run_stt.py --device cpu --compute-type int8

An engine spec is <faster-whisper model>:<language>, where language is "auto" (detect)
or a code such as "ta" or "en". Output: bakeoff_runs/stt-<time>/ with report.md,
summary.csv and transcripts.csv (read the transcripts — the numbers hide *how* it fails,
e.g. English words written in Tamil script).
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict

import httpx

from common import RECORDINGS, ROOT, mean, median, new_run_dir, p90, write_csv

from laptop_server.config import Settings
from laptop_server.voice.metrics import cer, wer
from laptop_server.voice.stt import FasterWhisperSTT, STTError

TARGET_CER = 0.15
DEFAULT_ENGINES = ["large-v3:auto", "large-v3:ta", "large-v3-turbo:auto"]
# Words AIRA hears often; a prompt nudges spelling. Compared with/without via --prompt.
VOCAB_PROMPT = "AIRA, Python, Java, Kali Linux, Nmap, GitHub, laptop, CPU, GPU, Wi-Fi. வணக்கம், இன்னைக்கு, பண்ணு."


def load_ollama(settings: Settings) -> None:
    r = httpx.post(f"{settings.ollama_base_url}/api/generate", timeout=300,
                   json={"model": settings.llm_model, "keep_alive": "30m", "options": {"num_ctx": settings.llm_num_ctx}})
    r.raise_for_status()
    print(f"Loaded {settings.llm_model} into Ollama so STT shares the GPU as it will in use.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", nargs="*", default=DEFAULT_ENGINES)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--compute-type", default="float16")
    ap.add_argument("--with-llm", action="store_true", help="load the Ollama model first")
    ap.add_argument("--prompt", action="store_true", help="pass a vocabulary prompt to Whisper")
    a = ap.parse_args()

    prompts_file = RECORDINGS / "prompts.json"
    if not prompts_file.exists():
        print("No recordings yet. Run: python scripts/bakeoff/record_stt.py")
        return 2
    prompts = {p["id"]: p for p in json.loads(prompts_file.read_text(encoding="utf-8"))}
    wavs = sorted(p for p in RECORDINGS.glob("*.wav") if p.stem in prompts)
    print(f"{len(wavs)} recordings found")
    if a.with_llm:
        load_ollama(Settings(_env_file=ROOT / ".env"))

    out = new_run_dir("stt")
    transcripts, summary = [], []
    loaded: dict[str, FasterWhisperSTT] = {}
    for spec in a.engines:
        model, _, lang = spec.partition(":")
        language = None if lang in ("", "auto") else lang
        try:
            if model not in loaded:
                print(f"\nLoading {model} on {a.device} ({a.compute_type})...")
                loaded[model] = FasterWhisperSTT(model, device=a.device, compute_type=a.compute_type,
                                                 initial_prompt=VOCAB_PROMPT if a.prompt else None)
                print(f"  loaded in {loaded[model].load_s:.1f}s")
            stt = loaded[model]
        except (STTError, RuntimeError, ValueError) as exc:
            print(f"[skipped] {spec}: {exc}")
            continue
        per_cat: dict = defaultdict(lambda: {"cer": [], "wer": []})
        lat, rtf = [], []
        for wav in wavs:
            p = prompts[wav.stem]
            t0 = time.perf_counter()
            try:
                tr = stt.transcribe(wav, language=language)
            except Exception as exc:  # one bad file must not kill the run
                print(f"  {wav.stem}: failed {exc}")
                continue
            c, w = cer(p["text"], tr.text), wer(p["text"], tr.text)
            for cat in (p["category"], "all"):
                per_cat[cat]["cer"].append(c)
                per_cat[cat]["wer"].append(w)
            lat.append(tr.latency_s)
            rtf.append(tr.real_time_factor)
            transcripts.append({"engine": spec, "id": p["id"], "category": p["category"], "reference": p["text"],
                                "hypothesis": tr.text, "detected_lang": tr.language, "cer": round(c, 3),
                                "wer": round(w, 3), "latency_s": round(tr.latency_s, 3),
                                "audio_s": round(tr.audio_s, 2), "wall_s": round(time.perf_counter() - t0, 3)})
            print(f"  {spec:<24} {p['id']}  CER {c:.2f}  {tr.latency_s:.2f}s  -> {tr.text[:60]}")
        row = {"engine": spec, "utterances": len(lat), "latency_p50_s": median(lat), "latency_p90_s": p90(lat),
               "rtf_mean": mean(rtf)}
        for cat in ("all", "english", "tamil", "tanglish"):
            row[f"cer_{cat}"] = mean(per_cat[cat]["cer"])
            row[f"wer_{cat}"] = mean(per_cat[cat]["wer"])
        summary.append(row)

    if not summary:
        return 1
    write_csv(out / "transcripts.csv", transcripts)
    write_csv(out / "summary.csv", summary)
    lines = ["# AIRA STT bake-off results", "",
             f"{len(wavs)} recordings · device {a.device}/{a.compute_type} · LLM loaded: {a.with_llm} · vocab prompt: {a.prompt}",
             f"Target: Tanglish CER ≤ {TARGET_CER:.0%}", "",
             "| Engine | CER all | CER English | CER Tamil | CER Tanglish | WER all | Latency p50 s | p90 s | RTF |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(summary, key=lambda r: r["cer_tanglish"] if r["cer_tanglish"] is not None else 9):
        lines.append(f"| {r['engine']} | {r['cer_all']} | {r['cer_english']} | {r['cer_tamil']} | {r['cer_tanglish']} | "
                     f"{r['wer_all']} | {r['latency_p50_s']} | {r['latency_p90_s']} | {r['rtf_mean']} |")
    best = min((r for r in summary if r["cer_tanglish"] is not None), key=lambda r: r["cer_tanglish"], default=None)
    if best:
        verdict = "meets" if best["cer_tanglish"] <= TARGET_CER else "does NOT meet"
        lines += ["", f"**Best on Tanglish:** {best['engine']} (CER {best['cer_tanglish']}) — {verdict} the target."]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    print("\n" + text + f"\nFull results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

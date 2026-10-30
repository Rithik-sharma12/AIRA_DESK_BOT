"""Benchmark Ollama models on the actual HP Omen hardware.

Measures, per model and context length:
  cold load time, time to first token (TTFT), generation tokens/s, prompt tokens/s,
  share of the model resident in VRAM (from /api/ps), peak GPU memory and utilisation
  (nvidia-smi), and peak system RAM (psutil).

Writes benchmarks/<timestamp>.json and a Markdown report next to it.

Examples (PowerShell, from the repo root, with the venv active):
  python scripts/benchmark_llm.py
  python scripts/benchmark_llm.py --models gemma4:e4b-it-qat gemma4:12b-it-qat --ctx 4096 8192 --runs 3
  python scripts/benchmark_llm.py --think off
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from laptop_server.agent.persona import build_system_prompt  # noqa: E402

# Targets used for the PASS/FAIL verdict. A spoken reply runs at roughly 3-4 tokens/s
# of speech, so anything comfortably above that keeps the TTS fed when streaming.
TARGET_TTFT_S = 1.5
TARGET_TOKENS_PER_S = 12.0
TARGET_GPU_FRACTION = 1.0

PROMPTS = {
    "english": "Hey AIRA, good morning! What can you help me with today?",
    "tamil": "வணக்கம் AIRA! இன்னைக்கு நான் Java படிக்கணும். ஒரு சின்ன study plan சொல்லு.",
    "tanglish": "Enna AIRA, Python la list comprehension na enna nu simple ah sollu.",
    "reasoning": "If I study Java for 45 minutes a day, how many hours is that in two weeks? Explain briefly.",
}


# ----------------------------------------------------------------------------- sampling
@dataclass
class Sampler:
    """Polls nvidia-smi and psutil in the background while a run is in progress."""

    interval_s: float = 0.5
    gpu_mem_mb: list[float] = field(default_factory=list)
    gpu_util: list[float] = field(default_factory=list)
    ram_used_gb: list[float] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            import psutil
        except ImportError:
            psutil = None
        while not self._stop.is_set():
            g = query_gpu()
            if g:
                self.gpu_mem_mb.append(g["mem_used_mb"])
                self.gpu_util.append(g["util_pct"])
            if psutil:
                self.ram_used_gb.append(psutil.virtual_memory().used / 1e9)
            self._stop.wait(self.interval_s)


def query_gpu() -> dict | None:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total,memory.used,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip().splitlines()[0]
        name, total, used, util, temp = [x.strip() for x in out.split(",")]
        return {"name": name, "mem_total_mb": float(total), "mem_used_mb": float(used),
                "util_pct": float(util), "temp_c": float(temp)}
    except (subprocess.SubprocessError, ValueError, IndexError, OSError):
        return None


# ------------------------------------------------------------------------------ ollama
def unload(client: httpx.Client, model: str) -> None:
    client.post("/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    time.sleep(1.0)


def stream_once(client: httpx.Client, model: str, system: str, prompt: str, num_ctx: int,
                max_tokens: int, think: bool | None) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "stream": True,
        "keep_alive": "10m",
        "options": {"num_ctx": num_ctx, "num_predict": max_tokens, "temperature": 0.7},
    }
    if think is not None:
        payload["think"] = think
    t0 = time.perf_counter()
    ttft = None
    text = []
    final: dict = {}
    with client.stream("POST", "/api/chat", json=payload, timeout=600) as r:
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}: {r.read().decode()[:300]}")
        for line in r.iter_lines():
            if not line.strip():
                continue
            d = json.loads(line)
            if "error" in d:
                raise RuntimeError(d["error"])
            piece = (d.get("message") or {}).get("content") or ""
            if piece and ttft is None:
                ttft = time.perf_counter() - t0
            text.append(piece)
            if d.get("done"):
                final = d
                break
    wall = time.perf_counter() - t0
    ns = 1e9
    eval_s = final.get("eval_duration", 0) / ns
    pe_s = final.get("prompt_eval_duration", 0) / ns
    return {
        "ttft_s": ttft,
        "wall_s": wall,
        "load_s": final.get("load_duration", 0) / ns,
        "prompt_tokens": final.get("prompt_eval_count", 0),
        "prompt_tok_s": final.get("prompt_eval_count", 0) / pe_s if pe_s else 0.0,
        "completion_tokens": final.get("eval_count", 0),
        "tok_s": final.get("eval_count", 0) / eval_s if eval_s else 0.0,
        "done_reason": final.get("done_reason"),
        "reply": "".join(text).strip(),
    }


def residency(client: httpx.Client, model: str) -> dict:
    for m in client.get("/api/ps").json().get("models", []):
        if m.get("name") == model or m.get("model") == model:
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            return {"size_gb": size / 1e9, "vram_gb": vram / 1e9, "gpu_fraction": vram / size if size else 0.0}
    return {"size_gb": 0.0, "vram_gb": 0.0, "gpu_fraction": 0.0}


# ------------------------------------------------------------------------------- main
def bench_config(client, model, num_ctx, runs, max_tokens, think, system) -> dict:
    print(f"\n=== {model}  num_ctx={num_ctx} ===")
    unload(client, model)
    baseline = query_gpu()
    sampler = Sampler()
    sampler.start()
    try:
        cold = stream_once(client, model, system, PROMPTS["english"], num_ctx, max_tokens, think)
        print(f"  cold: load={cold['load_s']:.1f}s ttft={cold['ttft_s'] or 0:.2f}s tok/s={cold['tok_s']:.1f}")
        res = residency(client, model)
        print(f"  resident: {res['size_gb']:.2f} GB, {res['gpu_fraction'] * 100:.0f}% in VRAM")
        warm: dict[str, list[dict]] = {}
        for name, prompt in PROMPTS.items():
            warm[name] = []
            for i in range(runs):
                r = stream_once(client, model, system, prompt, num_ctx, max_tokens, think)
                warm[name].append(r)
                print(f"  {name:<9} run {i + 1}: ttft={r['ttft_s'] or 0:.2f}s tok/s={r['tok_s']:.1f} "
                      f"({r['completion_tokens']} tok)")
    finally:
        sampler.stop()

    all_warm = [r for rs in warm.values() for r in rs]
    ttfts = [r["ttft_s"] for r in all_warm if r["ttft_s"] is not None]
    tps = [r["tok_s"] for r in all_warm if r["tok_s"] > 0]
    summary = {
        "model": model,
        "num_ctx": num_ctx,
        "cold_load_s": round(cold["load_s"], 2),
        "cold_ttft_s": round(cold["ttft_s"] or 0, 2),
        "warm_ttft_p50_s": round(statistics.median(ttfts), 3) if ttfts else None,
        "warm_ttft_max_s": round(max(ttfts), 3) if ttfts else None,
        "tok_s_mean": round(statistics.fmean(tps), 1) if tps else None,
        "tok_s_min": round(min(tps), 1) if tps else None,
        "prompt_tok_s_mean": round(statistics.fmean([r["prompt_tok_s"] for r in all_warm]), 1),
        "model_size_gb": round(res["size_gb"], 2),
        "gpu_fraction": round(res["gpu_fraction"], 3),
        "gpu_mem_peak_mb": max(sampler.gpu_mem_mb) if sampler.gpu_mem_mb else None,
        "gpu_mem_baseline_mb": baseline["mem_used_mb"] if baseline else None,
        "gpu_util_mean_pct": round(statistics.fmean(sampler.gpu_util), 1) if sampler.gpu_util else None,
        "ram_peak_gb": round(max(sampler.ram_used_gb), 2) if sampler.ram_used_gb else None,
    }
    summary["verdict"] = verdict(summary)
    return {"summary": summary, "cold": cold, "warm": warm}


def verdict(s: dict) -> str:
    problems = []
    if s["warm_ttft_p50_s"] is None or s["warm_ttft_p50_s"] > TARGET_TTFT_S:
        problems.append(f"TTFT p50 > {TARGET_TTFT_S}s")
    if s["tok_s_min"] is None or s["tok_s_min"] < TARGET_TOKENS_PER_S:
        problems.append(f"tok/s min < {TARGET_TOKENS_PER_S}")
    if s["gpu_fraction"] < TARGET_GPU_FRACTION:
        problems.append(f"only {s['gpu_fraction'] * 100:.0f}% on GPU")
    return "PASS" if not problems else "FAIL: " + "; ".join(problems)


def write_report(results: list[dict], meta: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"llm-{stamp}.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        f"# AIRA LLM benchmark — {meta['date']}",
        "",
        f"- Host: {meta['host']}  |  GPU: {meta['gpu']}  |  Ollama {meta['ollama_version']}",
        f"- Runs per prompt: {meta['runs']}  |  max tokens: {meta['max_tokens']}  |  think: {meta['think']}",
        f"- Targets: warm TTFT p50 ≤ {TARGET_TTFT_S}s, min ≥ {TARGET_TOKENS_PER_S} tok/s, 100% of model in VRAM",
        "",
        "| Model | ctx | cold load s | warm TTFT p50 s | tok/s mean (min) | size GB | % in VRAM | GPU peak MB | RAM peak GB | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r["summary"]
        lines.append(
            f"| {s['model']} | {s['num_ctx']} | {s['cold_load_s']} | {s['warm_ttft_p50_s']} | "
            f"{s['tok_s_mean']} ({s['tok_s_min']}) | {s['model_size_gb']} | {s['gpu_fraction'] * 100:.0f}% | "
            f"{s['gpu_mem_peak_mb']} | {s['ram_peak_gb']} | {s['verdict']} |"
        )
    lines += ["", "## Sample replies (warm run 1)", ""]
    for r in results:
        s = r["summary"]
        lines.append(f"### {s['model']} @ {s['num_ctx']}")
        for name, runs in r["warm"].items():
            lines.append(f"- **{name}**: {runs[0]['reply'][:300]}")
        lines.append("")
    md = out_dir / f"llm-{stamp}.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--models", nargs="+", default=["gemma4:e4b-it-qat", "gemma4:12b-it-qat"])
    ap.add_argument("--ctx", nargs="+", type=int, default=[4096])
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=200)
    ap.add_argument("--think", choices=["default", "on", "off"], default="default")
    ap.add_argument("--out", type=Path, default=ROOT / "benchmarks")
    a = ap.parse_args()
    think = {"default": None, "on": True, "off": False}[a.think]

    client = httpx.Client(base_url=a.ollama, timeout=30)
    try:
        version = client.get("/api/version").json().get("version")
        installed = {m["name"] for m in client.get("/api/tags").json().get("models", [])}
    except httpx.HTTPError as exc:
        print(f"Cannot reach Ollama at {a.ollama}: {exc}")
        return 2
    missing = [m for m in a.models if m not in installed]
    if missing:
        print("Not installed: " + ", ".join(missing))
        print("Installed: " + ", ".join(sorted(installed)))
        print("Pull with:  " + "  ;  ".join(f"ollama pull {m}" for m in missing))
        return 2

    gpu = query_gpu()
    meta = {
        "date": datetime.now().isoformat(timespec="seconds"),
        "host": f"{platform.node()} / {platform.system()} {platform.release()} / {platform.processor()}",
        "gpu": f"{gpu['name']} ({gpu['mem_total_mb']:.0f} MB)" if gpu else "nvidia-smi not found",
        "ollama_version": version,
        "runs": a.runs,
        "max_tokens": a.max_tokens,
        "think": a.think,
    }
    system = build_system_prompt()
    results = []
    for model in a.models:
        for ctx in a.ctx:
            try:
                results.append(bench_config(client, model, ctx, a.runs, a.max_tokens, think, system))
            except (RuntimeError, httpx.HTTPError) as exc:
                print(f"  FAILED: {exc}")
        unload(client, model)
    if not results:
        return 1
    md = write_report(results, meta, a.out)
    print(f"\nReport: {md}")
    for r in results:
        s = r["summary"]
        print(f"  {s['model']} @ {s['num_ctx']}: {s['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

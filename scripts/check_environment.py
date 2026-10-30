"""Pre-flight check for the AIRA laptop server. Run before starting the server.

  python scripts/check_environment.py

Prints PASS / WARN / FAIL for: Python version, dependencies, .env and API key,
Ollama reachability and version, the configured model, GPU/VRAM, Ollama exposure,
recommended Ollama settings, and whether AIRA's port is free.
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

results: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))
    print(f"[{status:<4}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    v = sys.version_info
    if v >= (3, 11):
        report("PASS", "Python", f"{v.major}.{v.minor}.{v.micro}")
    elif v >= (3, 10):
        report("WARN", "Python", f"{v.major}.{v.minor} works; 3.11 or 3.12 recommended for later voice packages")
    else:
        report("FAIL", "Python", "3.10+ required")

    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "pydantic_settings", "psutil"):
        try:
            importlib.import_module(mod)
        except ImportError:
            report("FAIL", f"package {mod}", "run: pip install -r requirements.txt")
    if not any(r[1].startswith("package") for r in results):
        report("PASS", "Python packages")

    from pydantic import ValidationError

    from laptop_server.config import Settings

    if not Path(".env").exists():
        report("WARN", ".env", "missing — run: python scripts/generate_api_key.py")
    try:
        s = Settings()
        s.validate_for_startup()
        report("PASS", "Configuration", f"model={s.llm_model} ctx={s.llm_num_ctx} bind={s.host}:{s.port}")
    except (ValidationError, ValueError) as exc:
        report("FAIL", "Configuration", str(exc).splitlines()[0])
        s = Settings(_env_file=None)

    import httpx

    try:
        ver = httpx.get(f"{s.ollama_base_url}/api/version", timeout=3).json().get("version")
        report("PASS", "Ollama reachable", f"{s.ollama_base_url} (version {ver})")
        names = sorted(m["name"] for m in httpx.get(f"{s.ollama_base_url}/api/tags", timeout=5).json()["models"])
        if s.llm_model in names or f"{s.llm_model}:latest" in names:
            report("PASS", "Model installed", s.llm_model)
        else:
            gemma = [n for n in names if n.startswith("gemma")]
            report("FAIL", "Model installed",
                   f"{s.llm_model} missing. Run: ollama pull {s.llm_model}. Installed gemma: {gemma or 'none'}")
    except httpx.HTTPError:
        report("FAIL", "Ollama reachable", f"nothing at {s.ollama_base_url}. Install from ollama.com and start it.")

    sys.path.insert(0, str(ROOT / "scripts"))
    from benchmark_llm import query_gpu

    gpu = query_gpu()
    if gpu:
        free = gpu["mem_total_mb"] - gpu["mem_used_mb"]
        report("PASS", "NVIDIA GPU", f"{gpu['name']}, {gpu['mem_total_mb']:.0f} MB total, {free:.0f} MB free now")
        if free < 6500:
            report("WARN", "Free VRAM",
                   f"{free:.0f} MB free. Close GPU-heavy apps (games, browsers with HW accel) before benchmarking.")
    else:
        report("WARN", "NVIDIA GPU", "nvidia-smi not found; Ollama may be running on CPU only")

    host = os.environ.get("OLLAMA_HOST", "")
    if host.startswith("0.0.0.0") or host.startswith("[::]"):
        report("FAIL", "Ollama exposure", f"OLLAMA_HOST={host} exposes Ollama to the network without auth. Unset it.")
    else:
        report("PASS", "Ollama exposure", "bound to localhost (default)")

    recs = {"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"}
    for k, want in recs.items():
        have = os.environ.get(k)
        if have != want:
            report("WARN", k, f"is {have!r}; {want!r} reduces VRAM use (set as a Windows user env var, restart Ollama)")

    with socket.socket() as sock:
        try:
            sock.bind((s.host if s.host != "localhost" else "127.0.0.1", s.port))
            report("PASS", "Port free", str(s.port))
        except OSError:
            report("WARN", "Port free", f"{s.port} in use — is AIRA already running?")

    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print(f"\n{fails} fail, {warns} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def windows_user_env(name: str) -> str | None:
    """Read a user-level env var saved in the registry (new terminals see it; this one may not)."""
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def main() -> int:
    v = sys.version_info
    if v >= (3, 11):
        report("PASS", "Python", f"{v.major}.{v.minor}.{v.micro}")
    elif v >= (3, 10):
        report("WARN", "Python", f"{v.major}.{v.minor} works; 3.11 or 3.12 recommended for later voice packages")
    else:
        report("FAIL", "Python", "3.10+ required")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    venv_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    missing = []
    for mod in ("fastapi", "uvicorn", "httpx", "pydantic", "pydantic_settings", "psutil"):
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        report("FAIL", "Python packages", "missing: " + ", ".join(missing))
        if not in_venv and venv_python.exists():
            print()
            print(f"  You are running the system Python ({sys.executable}),")
            print("  not the project's virtual environment. Use one of these instead:")
            if os.name == "nt":
                print("    PowerShell : .\\.venv\\Scripts\\Activate.ps1   (then re-run this script)")
                print("    cmd        : .venv\\Scripts\\activate.bat     (then re-run this script)")
                print("    either     : .venv\\Scripts\\python.exe scripts\\check_environment.py")
            else:
                print("    source .venv/bin/activate   (then re-run this script)")
        else:
            print("  Fix: pip install -r requirements.txt")
        return 1
    report("PASS", "Python packages", "virtual environment" if in_venv else "system Python")

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

    from laptop_server.llm.ollama_provider import is_loopback_url

    client = httpx.Client(timeout=5, trust_env=not is_loopback_url(s.ollama_base_url))
    try:
        ver = client.get(f"{s.ollama_base_url}/api/version", timeout=3).json().get("version")
        report("PASS", "Ollama reachable", f"{s.ollama_base_url} (version {ver})")
        names = sorted(m["name"] for m in client.get(f"{s.ollama_base_url}/api/tags").json()["models"])
        if s.llm_model in names or f"{s.llm_model}:latest" in names:
            report("PASS", "Model installed", s.llm_model)
        else:
            gemma = [n for n in names if n.startswith("gemma")]
            report("FAIL", "Model installed",
                   f"{s.llm_model} missing. Run: ollama pull {s.llm_model}  "
                   f"(or set AIRA_LLM_MODEL in .env to one you have; installed gemma: {gemma or 'none'})")
    except httpx.HTTPError as exc:
        report("FAIL", "Ollama reachable",
               f"no answer at {s.ollama_base_url} ({exc.__class__.__name__}). Start the Ollama app "
               "(Start menu) or run 'ollama serve' in another window, then re-run this check.")
    finally:
        client.close()

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
        have = os.environ.get(k) or windows_user_env(k)
        if have != want:
            report("WARN", k, f"is {have!r}; {want!r} reduces VRAM use (set as a Windows user env var, restart Ollama)")
        elif os.environ.get(k) != want:
            report("PASS", k, f"{want} saved for your user — quit and restart Ollama once so it applies")

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

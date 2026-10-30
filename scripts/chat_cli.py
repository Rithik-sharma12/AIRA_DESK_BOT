"""Talk to AIRA from the terminal (streams the reply as it is generated).

  python scripts/chat_cli.py
Commands:  /reset  start a new conversation    /status  server status    /quit  exit

Windows tip: run `chcp 65001` first and use Windows Terminal so Tamil script renders.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from laptop_server.config import Settings  # noqa: E402


def main() -> int:
    os.chdir(ROOT)
    s = Settings()
    host = "127.0.0.1" if s.host in ("0.0.0.0", "::") else s.host
    base = f"http://{host}:{s.port}/api/v1"
    headers = {"Authorization": f"Bearer {s.api_key.get_secret_value()}"}
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    session_id = None
    print("AIRA chat — /reset, /status, /quit\n")
    with httpx.Client(timeout=300) as client:
        while True:
            try:
                msg = input("you > ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not msg:
                continue
            if msg == "/quit":
                return 0
            if msg == "/reset":
                if session_id:
                    client.delete(f"{base}/sessions/{session_id}", headers=headers)
                session_id = None
                print("(new conversation)\n")
                continue
            if msg == "/status":
                print(json.dumps(client.get(f"{base}/status", headers=headers).json(), indent=2), "\n")
                continue
            body = {"message": msg, **({"session_id": session_id} if session_id else {})}
            try:
                with client.stream("POST", f"{base}/chat/stream", json=body, headers=headers) as r:
                    if r.status_code != 200:
                        print(f"[HTTP {r.status_code}] {r.read().decode()}\n")
                        continue
                    print("aira > ", end="", flush=True)
                    event = None
                    for line in r.iter_lines():
                        if line.startswith("event: "):
                            event = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            if event == "start":
                                session_id = data["session_id"]
                            elif event == "token":
                                print(data["text"], end="", flush=True)
                            elif event == "done":
                                st = data["stats"]
                                print(f"\n      [ttft {st['ttft_s']}s · {st['tokens_per_s']} tok/s"
                                      f"{' · load ' + str(st['load_s']) + 's' if st['load_s'] > 0.5 else ''}]\n")
                            elif event == "error":
                                print(f"\n[error] {data['message']}" + (f"\n  hint: {data['hint']}" if data.get("hint") else "") + "\n")
            except httpx.ConnectError:
                print(f"Cannot reach AIRA at {base}. Start it with:  python -m laptop_server\n")


if __name__ == "__main__":
    raise SystemExit(main())

"""Create .env from .env.example (if needed) and put a fresh random API key in it.

  python scripts/generate_api_key.py          # writes key into .env
  python scripts/generate_api_key.py --print  # only prints a key
"""

from __future__ import annotations

import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    key = secrets.token_urlsafe(32)  # 43 characters, 256 bits
    if "--print" in sys.argv:
        print(key)
        return 0
    env = ROOT / ".env"
    if not env.exists():
        shutil.copy(ROOT / ".env.example", env)
        print("Created .env from .env.example")
    text = env.read_text(encoding="utf-8")
    if re.search(r"^AIRA_API_KEY=.+$", text, flags=re.M) and "--force" not in sys.argv:
        print("AIRA_API_KEY already set in .env (use --force to replace it).")
        return 0
    if re.search(r"^AIRA_API_KEY=.*$", text, flags=re.M):
        text = re.sub(r"^AIRA_API_KEY=.*$", f"AIRA_API_KEY={key}", text, flags=re.M)
    else:
        text += f"\nAIRA_API_KEY={key}\n"
    env.write_text(text, encoding="utf-8")
    print("New API key written to .env. Keep this file private; it is git-ignored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

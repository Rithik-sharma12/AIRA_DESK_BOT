"""Shared helpers for the Phase 2 voice bake-off scripts."""

from __future__ import annotations

import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = HERE / "data"
RUNS = ROOT / "bakeoff_runs"            # git-ignored: audio, transcripts, ratings
RECORDINGS = ROOT / "bakeoff_recordings"  # git-ignored: your voice recordings


def load_tts_sentences() -> list[dict]:
    return json.loads((DATA / "tts_sentences.json").read_text(encoding="utf-8"))["sentences"]


def load_stt_prompts() -> list[dict]:
    spec = json.loads((DATA / "stt_prompts.json").read_text(encoding="utf-8"))
    prompts = list(load_tts_sentences()) if spec.get("include_tts_sentences") else []
    return prompts + spec["prompts"]


def new_run_dir(kind: str) -> Path:
    d = RUNS / f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or (list(rows[0].keys()) if rows else [])
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel shows Tamil correctly
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 3) if values else None


def p90(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))], 3)

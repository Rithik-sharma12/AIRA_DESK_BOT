"""Score a TTS bake-off run from raters' CSVs and apply the Phase 2 decision rule.

  python scripts/bakeoff/score_tts.py bakeoff_runs/tts-<time>
  python scripts/bakeoff/score_tts.py bakeoff_runs/tts-<time> --ratings path/to/ratings_*.csv

Decision rule (design doc, section E): among engines whose median time to first audio is
under 800 ms, pick the highest mean rating; an offline engine is kept as fallback.
Writes report.md into the run folder.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from common import mean, median, p90, read_csv

CRITERIA = ["naturalness", "tamil_pronunciation", "english_pronunciation", "sounds_like_aira"]
MAX_FIRST_AUDIO_S = 0.8
MIN_RATERS = 3


def collect(key: dict, rating_rows: list[dict]) -> tuple[dict, set[str]]:
    """engine -> criterion -> category -> list of scores; plus the set of raters."""
    scores: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    raters: set[str] = set()
    for row in rating_rows:
        info = key.get(row.get("blind_id", ""))
        if not info:
            continue
        rated_any = False
        for c in CRITERIA:
            v = (row.get(c) or "").strip()
            if v:
                n = float(v)
                if not 1 <= n <= 5:
                    raise ValueError(f"rating out of range 1-5: {row}")
                scores[info["engine"]][c][info["category"]].append(n)
                scores[info["engine"]][c]["all"].append(n)
                rated_any = True
        if rated_any:
            raters.add(row.get("rater") or "anon")
    return scores, raters


def summarize(scores: dict, latency_rows: list[dict]) -> list[dict]:
    lat: dict = defaultdict(lambda: {"first": [], "rtf": []})
    for r in latency_rows:
        lat[r["engine"]]["first"].append(float(r["first_audio_s"]))
        lat[r["engine"]]["rtf"].append(float(r["rtf"]))
    engines = sorted(set(scores) | set(lat))
    out = []
    for e in engines:
        crit_means = {c: mean(scores[e][c]["all"]) if e in scores else None for c in CRITERIA}
        present = [v for v in crit_means.values() if v is not None]
        row = {
            "engine": e,
            **crit_means,
            "overall": round(sum(present) / len(present), 3) if present else None,
            "tanglish_naturalness": mean(scores[e]["naturalness"]["tanglish"]) if e in scores else None,
            "first_audio_p50_s": median(lat[e]["first"]),
            "first_audio_p90_s": p90(lat[e]["first"]),
            "rtf_mean": mean(lat[e]["rtf"]),
        }
        row["meets_latency"] = row["first_audio_p50_s"] is not None and row["first_audio_p50_s"] < MAX_FIRST_AUDIO_S
        out.append(row)
    return out


def decide(summary: list[dict]) -> dict | None:
    eligible = [r for r in summary if r["meets_latency"] and r["overall"] is not None]
    return max(eligible, key=lambda r: r["overall"]) if eligible else None


def report(summary: list[dict], winner: dict | None, raters: set[str]) -> str:
    lines = ["# AIRA TTS bake-off results", ""]
    lines.append(f"Raters: {len(raters)} ({', '.join(sorted(raters)) or 'none'})"
                 + ("" if len(raters) >= MIN_RATERS else f" — fewer than {MIN_RATERS}; treat as provisional"))
    lines += ["", "| Engine | Naturalness | Tamil pron. | English pron. | Sounds like AIRA | Overall | Tanglish naturalness | First audio p50 s | p90 s | RTF | < 800 ms |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(summary, key=lambda r: -(r["overall"] or 0)):
        lines.append("| " + " | ".join(str(x) for x in [
            r["engine"], r["naturalness"], r["tamil_pronunciation"], r["english_pronunciation"], r["sounds_like_aira"],
            r["overall"], r["tanglish_naturalness"], r["first_audio_p50_s"], r["first_audio_p90_s"], r["rtf_mean"],
            "yes" if r["meets_latency"] else "no"]) + " |")
    lines.append("")
    lines.append(f"**Decision:** {winner['engine']} (overall {winner['overall']})." if winner
                 else "**Decision:** no engine meets the 800 ms first-audio limit with ratings — revisit settings or limits.")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--ratings", nargs="*", type=Path, help="rating CSVs (default: ratings_*.csv in the run folder)")
    a = ap.parse_args()
    key = json.loads((a.run_dir / "key.json").read_text(encoding="utf-8"))
    files = a.ratings or sorted(a.run_dir.glob("ratings_*.csv"))
    files = [f for f in files if f.name != "ratings_template.csv"]
    if not files:
        print("No rating files found. Put each rater's ratings_<name>.csv in the run folder.")
        return 2
    rows = [row for f in files for row in read_csv(f)]
    scores, raters = collect(key, rows)
    summary = summarize(scores, read_csv(a.run_dir / "latency.csv"))
    winner = decide(summary)
    text = report(summary, winner, raters)
    (a.run_dir / "report.md").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

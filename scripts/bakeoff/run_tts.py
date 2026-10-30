"""Phase 2 TTS bake-off: synthesise the 40-sentence test set with every configured engine.

Output (bakeoff_runs/tts-<time>/):
  audio/c001.wav ...        clips under blind ids (raters cannot tell which engine made them)
  listen.html               open in a browser: play each clip, rate it, download your ratings CSV
  ratings_template.csv      the same rating sheet for Excel users
  latency.csv               time to first audio, total time, real-time factor per clip
  key.json                  blind id -> engine + sentence (do not show raters)

Then:  python scripts/bakeoff/score_tts.py bakeoff_runs/tts-<time>

Examples:
  python scripts/bakeoff/run_tts.py
  python scripts/bakeoff/run_tts.py --engines azure-pallavi-ta_only azure-pallavi-neerja-split
  python scripts/bakeoff/run_tts.py --sentences en01 ta01 tg01      # quick smoke test
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import random
from pathlib import Path

from common import HERE, ROOT, load_tts_sentences, new_run_dir, write_csv

from laptop_server.config import Settings
from laptop_server.voice.tts import TTSError, VoiceSettings, create_tts

CRITERIA = ["naturalness", "tamil_pronunciation", "english_pronunciation", "sounds_like_aira"]


def build_engine(cfg: dict, settings: Settings):
    p = cfg["provider"]
    if p == "azure":
        return create_tts(
            "azure",
            key=settings.azure_speech_key.get_secret_value(),
            region=settings.azure_speech_region,
            ta_voice=cfg.get("ta_voice", settings.tts_ta_voice),
            en_voice=cfg.get("en_voice", settings.tts_en_voice),
            ta_style=cfg.get("ta_style"),
            en_style=cfg.get("en_style"),
        )
    if p == "piper":
        path = Path(cfg["model_path"])
        return create_tts("piper", model_path=path if path.is_absolute() else ROOT / path)
    if p in ("parler", "indic-parler"):
        return create_tts("parler", device=cfg.get("device", "cuda"))
    raise TTSError(f"unknown provider {p}")


def assign_blind_ids(pairs: list[tuple[str, dict]], seed: int) -> list[tuple[str, str, dict]]:
    order = list(pairs)
    random.Random(seed).shuffle(order)
    return [(f"c{i:03d}", eng, s) for i, (eng, s) in enumerate(order, 1)]


def listen_page(items: list[tuple[str, dict]]) -> str:
    rows = []
    for blind, s in items:
        cells = "".join(
            f'<td><select name="{c}"><option></option>' + "".join(f"<option>{n}</option>" for n in range(1, 6)) + "</select></td>"
            for c in CRITERIA
        )
        rows.append(
            f'<tr data-id="{blind}" data-cat="{s["category"]}"><td>{blind}</td><td>{html.escape(s["text"])}</td>'
            f'<td><audio controls preload="none" src="audio/{blind}.wav"></audio></td>{cells}'
            '<td><input name="notes" size="18"></td></tr>'
        )
    head = "".join(f"<th>{c.replace('_', ' ')}</th>" for c in CRITERIA)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>AIRA voice bake-off — blind rating</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;max-width:1300px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:6px;text-align:left;vertical-align:middle;font-size:14px}}th{{position:sticky;top:0;background:#fff}}
.bar{{display:flex;gap:12px;align-items:center;margin:12px 0}}</style></head><body>
<h1>AIRA voice bake-off — blind rating</h1>
<p>Listen to each clip and rate 1 (bad) to 5 (excellent). Leave Tamil pronunciation blank for English-only sentences and
English pronunciation blank for Tamil-only ones. "Sounds like AIRA" = a cheerful, youthful, clearly fictional Chennai companion.
Use headphones. When done, type your name and download — send the CSV back.</p>
<div class="bar">Your name: <input id="rater" placeholder="e.g. raj"><button onclick="dl()">Download my ratings</button><span id="done"></span></div>
<table><thead><tr><th>id</th><th>text</th><th>clip</th>{head}<th>notes</th></tr></thead><tbody>
{''.join(rows)}
</tbody></table>
<script>
const C = {json.dumps(CRITERIA)};
function q(v){{return '"' + String(v).replace(/"/g,'""') + '"';}}
function dl(){{
  const rater=(document.getElementById('rater').value||'anon').replace(/[^A-Za-z0-9_-]/g,'');
  const lines=[['rater','blind_id',...C,'notes'].join(',')];
  let n=0;
  document.querySelectorAll('tbody tr').forEach(tr=>{{
    const v=C.map(c=>tr.querySelector('[name='+c+']').value);
    if(v.some(x=>x)) n++;
    lines.push([rater,tr.dataset.id,...v,q(tr.querySelector('[name=notes]').value)].join(','));
  }});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(new Blob(['\\ufeff'+lines.join('\\n')],{{type:'text/csv'}}));
  a.download='ratings_'+rater+'.csv';a.click();
  document.getElementById('done').textContent=n+' clips rated';
}}
</script></body></html>"""


async def main_async(a) -> int:
    settings = Settings(_env_file=ROOT / ".env")
    sentences = load_tts_sentences()
    if a.sentences:
        sentences = [s for s in sentences if s["id"] in set(a.sentences)]
    configs = json.loads((HERE / "tts_engines.json").read_text(encoding="utf-8"))["engines"]
    if a.engines:
        configs = [c for c in configs if c["id"] in set(a.engines)]

    engines = {}
    for cfg in configs:
        try:
            engines[cfg["id"]] = (build_engine(cfg, settings), cfg)
            print(f"[ready]   {cfg['id']}")
        except (TTSError, ImportError, OSError) as exc:
            print(f"[skipped] {cfg['id']}: {exc}")
    if not engines:
        print("No engine available. Set AIRA_AZURE_SPEECH_KEY in .env or install an offline engine (see docs).")
        return 2

    out = new_run_dir("tts")
    items = assign_blind_ids([(eid, s) for eid in engines for s in sentences], a.seed)
    key, latency = {}, []
    failures = 0
    for blind, eid, s in sorted(items, key=lambda x: (x[1], x[2]["id"])):  # engine by engine (warm caches)
        engine, cfg = engines[eid]
        voice = VoiceSettings(**cfg.get("voice", {}))
        try:
            res = await engine.synthesize(s["text"], voice)
        except TTSError as exc:
            failures += 1
            print(f"  {eid} {s['id']}: FAILED {exc}")
            continue
        res.audio.save(out / "audio" / f"{blind}.wav")
        key[blind] = {"engine": eid, "sentence_id": s["id"], "category": s["category"], "text": s["text"]}
        latency.append({
            "engine": eid, "sentence_id": s["id"], "category": s["category"],
            "first_audio_s": round(res.first_audio_s, 3), "total_s": round(res.total_s, 3),
            "audio_s": round(res.audio.duration_s, 3), "rtf": round(res.real_time_factor, 3),
        })
        print(f"  {eid:<28} {s['id']}  first audio {res.first_audio_s:.2f}s  RTF {res.real_time_factor:.2f}")
    for engine, _ in engines.values():
        await engine.aclose()

    rated = [(b, s) for b, _, s in items if b in key]
    (out / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(out / "latency.csv", latency)
    write_csv(out / "ratings_template.csv",
              [{"rater": "", "blind_id": b, "category": s["category"], "text": s["text"], **{c: "" for c in CRITERIA}, "notes": ""}
               for b, s in rated])
    (out / "listen.html").write_text(listen_page(rated), encoding="utf-8")
    print(f"\n{len(key)} clips, {failures} failures -> {out}")
    print(f"Raters open {out / 'listen.html'}; then run: python scripts/bakeoff/score_tts.py \"{out}\"")
    return 0 if key else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", nargs="*", help="engine ids from tts_engines.json (default: all)")
    ap.add_argument("--sentences", nargs="*", help="sentence ids (default: all 40)")
    ap.add_argument("--seed", type=int, default=7, help="shuffle seed for blind ids")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

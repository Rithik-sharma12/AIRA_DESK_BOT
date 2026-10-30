"""Bake-off data integrity and an end-to-end TTS run + scoring with a fake engine."""

import asyncio
import csv
import json
import sys
from argparse import Namespace
from collections import Counter
from pathlib import Path

import pytest

BAKEOFF = Path(__file__).resolve().parents[2] / "scripts" / "bakeoff"
sys.path.insert(0, str(BAKEOFF))

import common  # noqa: E402
import run_tts  # noqa: E402
import score_tts  # noqa: E402

from laptop_server.voice.audio import AudioClip  # noqa: E402
from laptop_server.voice.text import has_latin, has_tamil  # noqa: E402
from laptop_server.voice.tts import SynthesisResult, TTSProvider  # noqa: E402


def test_tts_sentence_set_matches_spec():
    s = common.load_tts_sentences()
    assert Counter(x["category"] for x in s) == {"english": 15, "tamil": 10, "tanglish": 15}
    assert len({x["id"] for x in s}) == 40
    for x in s:
        if x["category"] == "english":
            assert not has_tamil(x["text"])
        elif x["category"] == "tamil":
            assert has_tamil(x["text"]) and not has_latin(x["text"])
        else:
            assert has_tamil(x["text"]) and has_latin(x["text"])


def test_stt_prompt_set_has_50_unique():
    p = common.load_stt_prompts()
    assert len(p) == 50 and len({x["id"] for x in p}) == 50


def test_engine_config_is_valid():
    cfg = json.loads((BAKEOFF / "tts_engines.json").read_text(encoding="utf-8"))["engines"]
    assert len({c["id"] for c in cfg}) == len(cfg)
    assert {c["provider"] for c in cfg} <= {"azure", "piper", "parler"}


class FakeTTS(TTSProvider):
    def __init__(self, delay):
        self.delay = delay

    async def synthesize(self, text, voice):
        return SynthesisResult(AudioClip.silence(0.4, 16000), self.delay, self.delay * 2)


def test_tts_run_then_score(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "RUNS", tmp_path)
    monkeypatch.setattr(run_tts, "new_run_dir", lambda kind: (tmp_path / kind).mkdir() or tmp_path / kind)
    speed = {"azure-pallavi-ta_only": 0.3, "azure-pallavi-neerja-split": 0.4, "piper-rasa-female": 1.2}
    monkeypatch.setattr(run_tts, "build_engine", lambda cfg, s: FakeTTS(speed[cfg["id"]]))

    rc = asyncio.run(run_tts.main_async(Namespace(engines=list(speed), sentences=["en01", "ta01", "tg01"], seed=1)))
    assert rc == 0
    run = tmp_path / "tts"
    key = json.loads((run / "key.json").read_text(encoding="utf-8"))
    assert len(key) == 9 and len(list((run / "audio").glob("*.wav"))) == 9
    html = (run / "listen.html").read_text(encoding="utf-8")
    assert "audio/c001.wav" in html and "azure" not in html  # blind: no engine names on the page

    # Three raters: the split engine sounds best, piper best of all but too slow.
    pref = {"azure-pallavi-ta_only": "3", "azure-pallavi-neerja-split": "4", "piper-rasa-female": "5"}
    for rater in ("a", "b", "c"):
        with (run / f"ratings_{rater}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rater", "blind_id", *score_tts.CRITERIA, "notes"])
            for blind, info in key.items():
                v = pref[info["engine"]]
                tamil = "" if info["category"] == "english" else v
                w.writerow([rater, blind, v, tamil, v, v, ""])

    scores, raters = score_tts.collect(key, [r for f in run.glob("ratings_*.csv") for r in common.read_csv(f)])
    summary = score_tts.summarize(scores, common.read_csv(run / "latency.csv"))
    winner = score_tts.decide(summary)
    assert raters == {"a", "b", "c"}
    assert winner["engine"] == "azure-pallavi-neerja-split"
    piper = next(r for r in summary if r["engine"] == "piper-rasa-female")
    assert piper["overall"] == 5.0 and piper["meets_latency"] is False
    assert "Decision:** azure-pallavi-neerja-split" in score_tts.report(summary, winner, raters)


def test_score_rejects_out_of_range():
    key = {"c001": {"engine": "e", "category": "english"}}
    with pytest.raises(ValueError):
        score_tts.collect(key, [{"blind_id": "c001", "naturalness": "9"}])

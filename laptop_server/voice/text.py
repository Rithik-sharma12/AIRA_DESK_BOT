"""Text handling for speech: sentence splitting (for streaming TTS) and Tamil/English
script segmentation (for choosing a voice per run of words).

Convention from the persona prompt: Tamil words are written in Tamil script and English
words in Latin script, so the script of each word tells us its language.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

TAMIL_RE = re.compile(r"[஀-௿]")
LATIN_RE = re.compile(r"[A-Za-z]")

Lang = Literal["ta", "en"]

_ABBREVIATIONS = {"e.g.", "i.e.", "etc.", "mr.", "mrs.", "ms.", "dr.", "vs.", "no.", "approx."}
# A terminator followed by whitespace (or end of text) ends a sentence. "3.5" and "v1.2" do not split.
_BOUNDARY_RE = re.compile(r"([.!?।]+[\"')\]]*)(\s+|$)")


def has_tamil(text: str) -> bool:
    return bool(TAMIL_RE.search(text))


def has_latin(text: str) -> bool:
    return bool(LATIN_RE.search(text))


def split_sentences(text: str) -> list[str]:
    """Split text into sentences; newlines are always boundaries."""
    sentences: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        start = 0
        for m in _BOUNDARY_RE.finditer(line):
            candidate = line[start : m.end(1)].strip()
            last_word = candidate.rsplit(" ", 1)[-1].lower()
            if last_word in _ABBREVIATIONS:
                continue
            if candidate:
                sentences.append(candidate)
            start = m.end()
        rest = line[start:].strip()
        if rest:
            sentences.append(rest)
    return sentences


class SentenceStream:
    """Turns a stream of LLM tokens into complete sentences as soon as each one ends.

    Phase 4 feeds every LLM chunk in and sends each yielded sentence to TTS immediately,
    so AIRA starts speaking before the whole reply is generated.
    """

    def __init__(self, min_chars: int = 2):
        self._buf = ""
        self.min_chars = min_chars

    def feed(self, chunk: str) -> Iterator[str]:
        self._buf += chunk
        while True:
            m = None
            for cand in _BOUNDARY_RE.finditer(self._buf):
                # Only a boundary followed by whitespace is final; at the very end more text may still come.
                if cand.end(2) > cand.end(1):
                    head = self._buf[: cand.end(1)].strip()
                    if head.rsplit(" ", 1)[-1].lower() in _ABBREVIATIONS:
                        continue
                    m = cand
                    break
            if m is None:
                return
            sentence = self._buf[: m.end(1)].strip()
            self._buf = self._buf[m.end() :]
            if len(sentence) >= self.min_chars:
                yield sentence

    def flush(self) -> Iterator[str]:
        rest = self._buf.strip()
        self._buf = ""
        if rest:
            yield rest


@dataclass(frozen=True)
class ScriptRun:
    lang: Lang
    text: str


def split_by_script(text: str, default: Lang = "ta") -> list[ScriptRun]:
    """Group consecutive words by language, decided per word by its script.

    A word containing any Tamil letter is Tamil (so "error-ஐ" goes to the Tamil voice,
    which can read the English stem; an English voice cannot read Tamil script).
    Words with neither script (numbers, punctuation) join the run before them, or the
    first run if they lead the text.
    """
    runs: list[list] = []  # [lang, [words]]
    pending: list[str] = []
    for word in text.split():
        if has_tamil(word):
            lang: Lang | None = "ta"
        elif has_latin(word):
            lang = "en"
        else:
            lang = None
        if lang is None:
            if runs:
                runs[-1][1].append(word)
            else:
                pending.append(word)
            continue
        if runs and runs[-1][0] == lang:
            runs[-1][1].append(word)
        else:
            runs.append([lang, pending + [word]])
            pending = []
    if not runs:
        return [ScriptRun(default, " ".join(pending))] if pending else []
    return [ScriptRun(lang, " ".join(words)) for lang, words in runs]


def dominant_language(text: str) -> Lang:
    """'en' only if the text has no Tamil script at all."""
    return "ta" if has_tamil(text) else "en"

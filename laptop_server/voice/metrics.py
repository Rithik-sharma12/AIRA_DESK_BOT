"""Speech-recognition error rates.

CER matters more than WER for Tamil: words are long and agglutinative, so one wrong
suffix fails a whole word under WER while costing only a character or two under CER.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence


def normalize(text: str) -> str:
    """NFC, lowercase, punctuation and symbols removed, whitespace collapsed.

    Tamil vowel signs and the virama are combining marks (Mn/Mc), not punctuation,
    so they are kept.
    """
    text = unicodedata.normalize("NFC", text).lower()
    kept = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("P") or cat.startswith("S"):
            kept.append(" ")
        else:
            kept.append(ch)
    return " ".join("".join(kept).split())


def edit_distance(ref: Sequence, hyp: Sequence) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize(reference).split(), normalize(hypothesis).split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)

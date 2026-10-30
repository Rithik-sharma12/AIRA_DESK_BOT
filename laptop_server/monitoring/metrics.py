"""Rolling performance metrics for the acceptance criteria (TTFT, tokens/s, load time)."""

from __future__ import annotations

import statistics
from collections import deque
from typing import Any

from ..llm.base import GenerationStats


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


class MetricsRecorder:
    def __init__(self, window: int = 200):
        self._items: deque[GenerationStats] = deque(maxlen=window)
        self.errors: dict[str, int] = {}

    def record(self, stats: GenerationStats) -> None:
        self._items.append(stats)

    def record_error(self, code: str) -> None:
        self.errors[code] = self.errors.get(code, 0) + 1

    def summary(self) -> dict[str, Any]:
        items = list(self._items)
        warm = [s for s in items if s.load_s < 0.5]  # exclude requests that paid a model load
        ttft = [s.ttft_s for s in warm if s.ttft_s is not None]
        tps = [s.tokens_per_s for s in warm if s.tokens_per_s > 0]
        return {
            "requests": len(items),
            "warm_requests": len(warm),
            "ttft_s": {
                "p50": round(_percentile(ttft, 50), 3),
                "p95": round(_percentile(ttft, 95), 3),
            },
            "tokens_per_s": {
                "mean": round(statistics.fmean(tps), 2) if tps else 0.0,
                "min": round(min(tps), 2) if tps else 0.0,
            },
            "max_load_s": round(max((s.load_s for s in items), default=0.0), 2),
            "errors": dict(self.errors),
        }

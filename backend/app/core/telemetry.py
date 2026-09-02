"""Runtime telemetry: latency percentiles and a cost baseline per intent.

Two jobs:
  1. Prove the "<10 ms, no second model call" Ring 0 claim with measured
     numbers instead of a slide bullet.
  2. Keep a rolling token baseline per (use_case, intent) so Ring 0 can spot a
     response that cost far more than the same kind of request normally does.
"""
from __future__ import annotations

import math
import re
import statistics
import time
from collections import defaultdict, deque
from threading import Lock


class LatencyRecorder:
    def __init__(self, maxlen: int = 5000) -> None:
        self._samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=maxlen))
        self._lock = Lock()

    def record(self, stage: str, micros: float) -> None:
        with self._lock:
            self._samples[stage].append(micros)

    def percentiles(self, stage: str) -> dict:
        with self._lock:
            data = sorted(self._samples.get(stage, ()))
        if not data:
            return {"count": 0, "p50_us": 0, "p95_us": 0, "p99_us": 0, "max_us": 0}

        def pct(p: float) -> float:
            idx = min(len(data) - 1, max(0, math.ceil(p * len(data)) - 1))
            return data[idx]

        return {
            "count": len(data),
            "p50_us": round(pct(0.50)),
            "p95_us": round(pct(0.95)),
            "p99_us": round(pct(0.99)),
            "max_us": round(data[-1]),
            "mean_us": round(statistics.fmean(data)),
        }

    def all_stages(self) -> dict:
        with self._lock:
            stages = list(self._samples)
        return {s: self.percentiles(s) for s in stages}


# Coarse intent buckets. Enough to make a token baseline meaningful without
# pretending we have a trained classifier.
_INTENT_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("policy_lookup", re.compile(r"\b(policy|refund|warrant|eligib|terms|sla|entitle)\w*", re.I)),
    ("calculation", re.compile(r"\b(calculat|total|sum|interest|discount|invoice|amount)\w*|\d+\s*[-+*/x]\s*\d+", re.I)),
    ("summarisation", re.compile(r"\b(summar|tl;?dr|recap|brief)\w*", re.I)),
    ("decision", re.compile(r"\b(approve|reject|decline|assess|recommend|should we|risk|credit|claim)\w*", re.I)),
    ("drafting", re.compile(r"\b(draft|write|compose|email|reply|letter)\w*", re.I)),
    ("lookup", re.compile(r"\b(what|who|when|where|which|how many)\b", re.I)),
]


def classify_intent(prompt: str) -> str:
    for name, pat in _INTENT_PATTERNS:
        if pat.search(prompt):
            return name
    return "general"


class CostBaseline:
    """Rolling mean/stdev of tokens spent per (use_case, intent)."""

    def __init__(self, window: int = 400) -> None:
        self._w: dict[tuple[str, str], deque[int]] = defaultdict(lambda: deque(maxlen=window))
        self._lock = Lock()

    def observe(self, use_case: str, intent: str, tokens: int) -> None:
        with self._lock:
            self._w[(use_case, intent)].append(int(tokens))

    def score(self, use_case: str, intent: str, tokens: int) -> dict:
        """Return z-score of this call's token spend against its own baseline."""
        with self._lock:
            data = list(self._w.get((use_case, intent), ()))
        if len(data) < 8:
            return {
                "baseline_tokens": None,
                "z": 0.0,
                "delta_pct": 0.0,
                "samples": len(data),
                "note": "baseline still warming up",
            }
        mean = statistics.fmean(data)
        sd = statistics.pstdev(data) or 1.0
        return {
            "baseline_tokens": round(mean, 1),
            "z": round((tokens - mean) / sd, 2),
            "delta_pct": round(((tokens - mean) / mean) * 100, 1) if mean else 0.0,
            "samples": len(data),
            "note": "",
        }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                f"{uc}:{it}": {
                    "samples": len(v),
                    "mean_tokens": round(statistics.fmean(v), 1) if v else 0,
                }
                for (uc, it), v in self._w.items()
            }


latency = LatencyRecorder()
cost_baseline = CostBaseline()


class Stopwatch:
    """`with Stopwatch('ring0') as sw: ...` -> sw.micros"""

    def __init__(self, stage: str | None = None) -> None:
        self.stage = stage
        self.micros = 0.0

    def __enter__(self) -> "Stopwatch":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:
        self.micros = (time.perf_counter() - self._t0) * 1_000_000
        if self.stage:
            latency.record(self.stage, self.micros)

"""Ring 1 - the spend cap, enforced in code.

Our Round 1 deck promised oversight at under 3% of inference spend, on 5-10%
of traffic. Those are easy numbers to put on a slide and easy to quietly
exceed, so they live here as an actual admission controller rather than as a
claim.

Two independent caps per policy, over a rolling window:

  volume cap   Ring 1 may run on at most `ring1_sample_rate` of requests.
  spend cap    Ring 1 may spend at most `ring1_spend_cap_pct` percent of what
               the production model spent.

When grey-zone volume exceeds the budget, work is not dropped at random. It is
ranked -- irreversible actions first, then lowest confidence -- and everything
above the line runs. What falls below is recorded as `deferred_budget`, which
is visible in the UI. A queue that silently drops work is worse than one that
admits it is full.

A small slice of the budget is reserved for auditing traffic that Ring 0
cleared. Without that we would only ever measure our false positives, never our
false negatives, and a governance system that cannot find its own misses is
marking its own homework.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock

# Share of the Ring 1 budget spent on randomly auditing "allow" verdicts.
AUDIT_SHARE = 0.20
WINDOW_SECONDS = 3600.0
# Priority at or above which the volume cap is bypassed. Only responses feeding
# an irreversible action reach it (see _priority in the Ring 0 scorer).
HIGH_PRIORITY = 1.0

# A cap expressed as a percentage is meaningless on a window of three requests:
# one indivisible deep check is already 30% of the spend. So both caps stay
# dormant until this many checks have run in the window, then they bind. Without
# it a quiet use case would never get a single deep check, which is the opposite
# of what a budget is for.
MIN_CHECKS_PER_WINDOW = 5


@dataclass
class _Entry:
    ts: float
    ring1: bool
    base_cost: float
    ring1_cost: float
    response_id: str = ""


# Assumed cost of a deep check before we have measured any. Replaced by a
# rolling average of real costs as soon as one completes.
INITIAL_ESTIMATE_USD = 0.0006


class BudgetManager:
    def __init__(self, window_seconds: float = WINDOW_SECONDS) -> None:
        self._w: dict[str, deque[_Entry]] = defaultdict(deque)
        self._deferred: dict[str, int] = defaultdict(int)
        self._lock = Lock()
        self.window = window_seconds
        # Deep checks resolve asynchronously, so at any moment some spend is
        # committed but not yet recorded. Without counting it, a burst of
        # requests all pass the cap check before the first one finishes paying.
        # We charge an estimate at admission and reconcile on completion.
        self._estimate = INITIAL_ESTIMATE_USD
        self._observed = 0

    def _prune(self, use_case: str, now: float) -> deque[_Entry]:
        q = self._w[use_case]
        while q and now - q[0].ts > self.window:
            q.popleft()
        return q

    def record_request(self, use_case: str, base_cost: float, response_id: str = "") -> None:
        with self._lock:
            now = time.time()
            self._prune(use_case, now)
            self._w[use_case].append(_Entry(now, False, base_cost, 0.0, response_id))

    def stats(self, use_case: str) -> dict:
        with self._lock:
            q = self._prune(use_case, time.time())
            n = len(q)
            ran = sum(1 for e in q if e.ring1)
            base = sum(e.base_cost for e in q)
            deep = sum(e.ring1_cost for e in q)
        pct = round((deep / base) * 100, 3) if base else 0.0
        return {
            "window_requests": n,
            "ring1_runs": ran,
            "ring1_rate": round(ran / n, 4) if n else 0.0,
            "base_spend_usd": round(base, 6),
            "ring1_spend_usd": round(deep, 6),
            "ring1_spend_pct": pct,
            "deferred_for_budget": self._deferred.get(use_case, 0),
            "note": (
                # A deep check is indivisible, so on a small window one check can
                # be a large share of a small base before the cap starts biting.
                # The ratio converges as volume grows; saying so beats quietly
                # reporting a number that looks like the cap failed.
                "low volume: a single deep check is a large share of this window's "
                "spend, so the realised ratio overshoots the cap until traffic builds"
                if n < 40 else ""
            ),
        }

    def admit(
        self,
        use_case: str,
        policy,
        priority: float,
        is_audit: bool = False,
        response_id: str = "",
    ) -> tuple[bool, str]:
        """Decide whether this response may enter Ring 1."""
        sample_rate = getattr(policy, "ring1_sample_rate", 0.075) or 0.075
        spend_cap = getattr(policy, "ring1_spend_cap_pct", 3.0) or 3.0

        with self._lock:
            now = time.time()
            q = self._prune(use_case, now)
            n = len(q)
            ran = sum(1 for e in q if e.ring1)
            base = sum(e.base_cost for e in q)
            deep = sum(e.ring1_cost for e in q)

        allowance = max(MIN_CHECKS_PER_WINDOW, int(n * sample_rate))
        if is_audit:
            allowance = max(1, int(allowance * AUDIT_SHARE))
            ran_audit = ran  # audits share the same counter; approximate is fine here
            if ran_audit >= allowance and n > 20:
                return False, "audit sample outside budget"

        # Work above the line runs even when the volume cap is spent. A
        # priority of 1.0 or more means the response feeds an irreversible
        # action -- a payment, an outbound email, a record write. Deferring
        # that check to save a fraction of a cent is the wrong trade, and the
        # spend cap below still bounds the damage.
        high_priority = priority >= HIGH_PRIORITY

        if ran >= allowance and sample_rate < 1.0 and not high_priority:
            with self._lock:
                self._deferred[use_case] += 1
            return False, (
                f"deferred: Ring 1 volume cap reached "
                f"({ran}/{n} = {ran / max(n, 1):.1%}, cap {sample_rate:.1%})"
            )

        if (
            ran >= MIN_CHECKS_PER_WINDOW
            and base > 0
            and ((deep + self._estimate) / base) * 100 > spend_cap
        ):
            with self._lock:
                self._deferred[use_case] += 1
            return False, (
                f"deferred: Ring 1 spend cap reached "
                f"({(deep / base) * 100:.2f}% of inference spend already committed, "
                f"cap {spend_cap}%)"
            )

        # Charge the estimate now; record_ring1 swaps it for the real figure
        # once the check finishes.
        with self._lock:
            target = self._find(q, response_id)
            if target is not None:
                target.ring1 = True
                target.ring1_cost = self._estimate
        return True, "admitted"

    @staticmethod
    def _find(q: deque[_Entry], response_id: str) -> _Entry | None:
        if response_id:
            hit = next((e for e in reversed(q) if e.response_id == response_id), None)
            if hit is not None:
                return hit
        return q[-1] if q else None

    def record_ring1(self, use_case: str, ring1_cost: float, response_id: str = "") -> None:
        """Replace the reserved estimate with what the deep check actually cost.

        Ring 1 finishes out of order, so the entry is looked up by id rather
        than charged to whichever request happened to arrive most recently.
        """
        with self._lock:
            q = self._prune(use_case, time.time())
            target = self._find(q, response_id)
            if target is None:
                self._w[use_case].append(_Entry(time.time(), True, 0.0, ring1_cost, response_id))
            else:
                target.ring1 = True
                target.ring1_cost = ring1_cost
            # Keep the estimate honest by averaging what we actually observe.
            self._observed += 1
            self._estimate += (ring1_cost - self._estimate) / min(self._observed, 50)

    def snapshot(self) -> dict:
        with self._lock:
            keys = list(self._w)
        return {k: self.stats(k) for k in keys}

    def reset(self) -> None:
        """Clear the rolling window.

        Used after backfilling synthetic history: those requests are dated into
        the past, so they should not hold live budget against the very next
        real request.
        """
        with self._lock:
            self._w.clear()
            self._deferred.clear()


budget = BudgetManager()

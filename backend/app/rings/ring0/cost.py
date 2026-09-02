"""Ring 0 - the cost lane.

Most oversight tools watch only for harm. Our Round 1 pitch had three lanes,
and this is the one that pays for the other two: retries, oversized models and
bloated context leak spend that no dashboard attributes to anything.

Three cheap signals, all computed from data we already have:

  spend anomaly   this response used far more tokens than the same kind of
                  request normally does for this use case (z-score against a
                  rolling baseline).
  retry loop      the same session asked a near-identical question again,
                  which usually means the previous answer was unusable.
  over-modelling  a trivially routine intent was served by a premium model,
                  where a small model would have done -- the single largest
                  source of recoverable spend in most enterprise fleets.
"""
from __future__ import annotations

import difflib
import re

from app.core.providers.pricing import PRICE_PER_MTOK
from app.core.telemetry import classify_intent, cost_baseline

# Intents that almost never need a frontier model.
CHEAP_INTENTS = {"lookup", "policy_lookup", "summarisation"}
# Output price per Mtok above which a model counts as "premium".
PREMIUM_OUTPUT_PRICE = 8.0

_NORM = re.compile(r"[^a-z0-9 ]+")


def _normalise(prompt: str) -> str:
    return _NORM.sub(" ", prompt.lower()).strip()


def check_cost(
    prompt: str,
    use_case: str,
    model_name: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    session_history: list[str] | None = None,
    z_threshold: float = 2.5,
) -> dict:
    intent = classify_intent(prompt)
    total_tokens = tokens_in + tokens_out
    baseline = cost_baseline.score(use_case, intent, total_tokens)
    cost_baseline.observe(use_case, intent, total_tokens)

    flags: list[str] = []
    if baseline["baseline_tokens"] is not None and baseline["z"] >= z_threshold:
        flags.append(
            f"spend anomaly: {total_tokens} tokens vs a baseline of "
            f"{baseline['baseline_tokens']:.0f} for {intent} in {use_case} "
            f"(z={baseline['z']}, +{baseline['delta_pct']}%)"
        )

    # Retry detection.
    retry_similarity = 0.0
    if session_history:
        me = _normalise(prompt)
        for prev in session_history[-5:]:
            ratio = difflib.SequenceMatcher(None, me, _normalise(prev)).ratio()
            retry_similarity = max(retry_similarity, ratio)
        if retry_similarity >= 0.86:
            flags.append(
                f"probable retry: this prompt is {retry_similarity:.0%} identical to an "
                "earlier turn in the same session -- the previous answer was likely unusable"
            )

    # Over-modelling.
    _, out_price = PRICE_PER_MTOK.get(model_name, (1.0, 4.0))
    over_modelled = intent in CHEAP_INTENTS and out_price >= PREMIUM_OUTPUT_PRICE
    potential_saving = 0.0
    if over_modelled:
        cheap_in, cheap_out = PRICE_PER_MTOK["gpt-4o-mini"]
        cheap_cost = (tokens_in / 1e6) * cheap_in + (tokens_out / 1e6) * cheap_out
        potential_saving = max(0.0, cost_usd - cheap_cost)
        flags.append(
            f"right-size candidate: '{intent}' served by {model_name}; a small model "
            f"would have cost about ${cheap_cost:.5f} instead of ${cost_usd:.5f}"
        )

    return {
        "intent": intent,
        "tokens_total": total_tokens,
        "cost_usd": round(cost_usd, 6),
        "baseline": baseline,
        "z": baseline["z"],
        "retry_similarity": round(retry_similarity, 3),
        "is_retry": retry_similarity >= 0.86,
        "over_modelled": over_modelled,
        "potential_saving_usd": round(potential_saving, 6),
        "flags": flags,
        # The cost lane advises; it never blocks an answer on its own.
        "advisory_only": True,
    }

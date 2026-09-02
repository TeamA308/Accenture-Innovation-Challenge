"""Ring 0 orchestration.

Two entry points, because Ring 0 runs twice per request:

`scan_stream_partial` runs on the text produced *so far*, every few chunks,
while the model is still typing. It only runs the two deterministic detectors
(personal data and credentials) because those are the only findings certain
enough to justify cutting a stream off mid-sentence. Catching a leaked
credential after the user has already read it is not much of a control.

`run_ring0` runs once on the finished text: every detector, then the decision
engine. It is measured in microseconds and reported that way -- the "<10 ms on
100% of traffic" claim should be checkable on the dashboard, not taken on faith.
"""
from __future__ import annotations

import logging

from app.core.telemetry import Stopwatch
from app.rings.ring0 import cost as cost_mod
from app.rings.ring0 import grounding as grounding_mod
from app.rings.ring0 import pii as pii_mod
from app.rings.ring0 import schema_check, secrets as secrets_mod, uncertainty as unc_mod
from app.rings.ring0.scorer import score_ring0

log = logging.getLogger("controlplane.ring0")

# Cap the text we scan so a runaway response cannot blow the latency budget.
MAX_SCAN_CHARS = 20_000


def _safe(name: str, fn, fallback):
    """Never let one detector take down the request.

    A crashed detector degrades to 'unverified' and says so. Failing open
    silently would be the worst possible behaviour for a governance layer, and
    failing closed would take the product down over a bad regex.
    """
    try:
        return fn(), None
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("ring0 detector %s failed", name)
        return fallback, f"{name} unavailable ({type(exc).__name__}); treated as unverified"


def scan_stream_partial(text: str, policy=None) -> dict | None:
    """Cheap mid-stream check. Returns a violation dict, or None to keep going."""
    if len(text) < 24:
        return None
    threshold = getattr(policy, "pii_block_threshold", 0.85) if policy else 0.85
    blocked = set(getattr(policy, "blocked_entity_types", []) or []) if policy else set()

    snippet = text[-MAX_SCAN_CHARS:]
    hits = pii_mod.detect_pii(snippet, engine="deterministic")
    sec = secrets_mod.detect_secrets(snippet)

    bad_pii = [h for h in hits
               if h["score"] >= threshold and (not blocked or h["entity_type"] in blocked)]
    bad_secrets = [s for s in sec if s["score"] >= 0.80]
    if not bad_pii and not bad_secrets:
        return None
    return {
        "kind": "deterministic_violation",
        "entity_types": sorted({h["entity_type"] for h in bad_pii}
                               | {s["secret_type"] for s in bad_secrets}),
        "pii": bad_pii,
        "secrets": bad_secrets,
    }


def run_ring0(
    *,
    prompt: str,
    response_text: str,
    context_docs: list[str] | None,
    policy,
    use_case: str,
    model_name: str,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: float = 0.0,
    token_logprobs: list[float] | None = None,
    expected_schema: dict | None = None,
    is_reversible: bool = True,
    downstream_action: str = "draft",
    session_history: list[str] | None = None,
    conversation_risk: float = 0.0,
) -> tuple[dict, dict, int]:
    """Run every inline check. Returns (signals, verdict, elapsed_microseconds)."""
    text = (response_text or "")[:MAX_SCAN_CHARS]
    degraded: list[str] = []

    with Stopwatch("ring0") as sw:
        pii_hits, err = _safe("pii", lambda: pii_mod.detect_pii(text), [])
        if err:
            degraded.append(err)

        secret_hits, err = _safe("secrets", lambda: secrets_mod.detect_secrets(text), [])
        if err:
            degraded.append(err)

        schema_result, err = _safe(
            "schema_arithmetic",
            lambda: schema_check.check_arithmetic_and_schema(text, expected_schema),
            {"valid": True, "errors": [], "arithmetic": [], "arithmetic_failed": 0,
             "schema": {"applicable": False, "valid": True, "errors": []}},
        )
        if err:
            degraded.append(err)

        unc, err = _safe(
            "uncertainty",
            lambda: unc_mod.estimate_uncertainty(text, token_logprobs),
            {"score": 0.5, "method": "unavailable", "assertiveness": 0.5},
        )
        if err:
            degraded.append(err)

        ground, err = _safe(
            "grounding",
            lambda: grounding_mod.check_grounding(text, context_docs),
            {"score": None, "status": "ungroundable", "claims": [], "supported": 0,
             "unsupported": 0, "contradicted": 0, "note": "detector unavailable"},
        )
        if err:
            degraded.append(err)

        cost_signal, err = _safe(
            "cost",
            lambda: cost_mod.check_cost(
                prompt, use_case, model_name, tokens_in, tokens_out, cost_usd,
                session_history=session_history,
                z_threshold=getattr(policy, "cost_anomaly_z", 2.5) if policy else 2.5,
            ),
            {"flags": [], "intent": "unknown", "advisory_only": True},
        )
        if err:
            degraded.append(err)

        verdict = score_ring0(
            pii=pii_hits,
            secrets=secret_hits,
            schema_result=schema_result,
            uncertainty=unc,
            grounding=ground,
            cost=cost_signal,
            policy=policy,
            is_reversible=is_reversible,
            downstream_action=downstream_action,
            conversation_risk=conversation_risk,
        )

    if degraded:
        # A degraded check must escalate, never quietly pass.
        verdict["reasons"] = degraded + verdict["reasons"]
        verdict["degraded"] = degraded
        if verdict["action"] == "allow":
            verdict["action"] = "flag"
            verdict["driving_signal"] = "degraded_detector"
            verdict["ring1_recommended"] = True

    signals = {
        "pii": pii_hits,
        "secrets": secret_hits,
        "schema_arithmetic": schema_result,
        "uncertainty": unc,
        "grounding": ground,
        "cost": cost_signal,
        "elapsed_us": round(sw.micros),
        "degraded": degraded,
    }
    return signals, verdict, round(sw.micros)

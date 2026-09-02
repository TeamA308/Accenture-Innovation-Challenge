"""Ring 0 - the decision engine.

Signals in, one verdict out. Two rules govern everything here.

Rule 1: certainty decides the *kind* of response.
    A deterministic violation (a validated card number, a live API key) is not
    a probability. We block and redact it, and no threshold argues with that.
    Everything else is probabilistic, and probabilistic findings do not get to
    stop a user seeing an answer.

Rule 2: reversibility decides the *severity*.
    The same uncertain claim is a footnote in a draft an employee will read,
    and an incident in a payment instruction. So the matrix is:

        deterministic violation      -> block and redact
        probabilistic, reversible    -> annotate the claim, never block
        probabilistic, irreversible  -> gate the commit, not the tokens
        mechanical error             -> repair, never rewrite substance

    "Gate" is the interesting one. The user still sees every token. What stops
    is the downstream commit -- the payment, the outbound email, the database
    write -- until Ring 1 or a human clears it. Nobody waits for a check they
    did not need.

Every number this module compares against comes from the Policy row, so the
same signals produce different verdicts for a customer-facing bot and an
internal copilot. That is the whole product in one function.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# How much each signal can subtract from a starting confidence of 1.0.
WEIGHTS = {
    "grounding": 0.42,
    "uncertainty": 0.22,
    "arithmetic": 0.30,
    "schema": 0.20,
    "pii": 0.35,
    "secret": 0.40,
    "ungroundable": 0.18,
}

IRREVERSIBLE_ACTIONS = {"payment", "email_send", "db_write", "api_commit", "order", "disbursal"}


@dataclass
class Verdict:
    confidence: float = 1.0
    action: str = "allow"
    reasons: list[str] = field(default_factory=list)
    # Which signal was decisive. Ring 2's tuner needs to know which threshold
    # to move when a human says we were wrong.
    driving_signal: str = "none"
    deterministic_violation: bool = False
    needs_redaction: bool = False
    redact_entity_types: list[str] = field(default_factory=list)
    repair_notes: list[str] = field(default_factory=list)
    ring1_recommended: bool = False
    ring1_priority: float = 0.0
    gate_required: bool = False
    penalties: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "confidence": round(self.confidence, 3),
            "action": self.action,
            "reasons": self.reasons,
            "driving_signal": self.driving_signal,
            "deterministic_violation": self.deterministic_violation,
            "needs_redaction": self.needs_redaction,
            "redact_entity_types": self.redact_entity_types,
            "repair_notes": self.repair_notes,
            "ring1_recommended": self.ring1_recommended,
            "ring1_priority": round(self.ring1_priority, 3),
            "gate_required": self.gate_required,
            "penalties": {k: round(v, 3) for k, v in self.penalties.items()},
        }


def _policy_get(policy, name, default):
    if policy is None:
        return default
    return getattr(policy, name, default) if not isinstance(policy, dict) \
        else policy.get(name, default)


def score_ring0(
    pii: list[dict],
    secrets: list[dict],
    schema_result: dict,
    uncertainty: dict,
    grounding: dict,
    cost: dict | None = None,
    policy=None,
    is_reversible: bool = True,
    downstream_action: str = "draft",
    conversation_risk: float = 0.0,
) -> dict:
    """Combine every Ring 0 signal into a single, explainable verdict."""

    pii_block_threshold = _policy_get(policy, "pii_block_threshold", 0.85)
    grounding_threshold = _policy_get(policy, "grounding_flag_threshold", 0.55)
    uncertainty_threshold = _policy_get(policy, "uncertainty_flag_threshold", 0.55)
    confidence_block = _policy_get(policy, "confidence_block_threshold", 0.25)
    blocked_types = set(_policy_get(policy, "blocked_entity_types", []) or [])
    risk_tolerance = _policy_get(policy, "risk_tolerance", "medium")

    v = Verdict()
    penalties: dict[str, float] = {}

    # ---------------------------------------------------------------- 1. hard
    # Deterministic violations. Certain, so they act without a threshold debate.
    blocking_pii = [
        h for h in pii
        if h["score"] >= pii_block_threshold
        and (not blocked_types or h["entity_type"] in blocked_types)
    ]
    hard_secrets = [s for s in secrets if s["score"] >= 0.80]

    if pii:
        penalties["pii"] = WEIGHTS["pii"] * max(h["score"] for h in pii)
    if secrets:
        penalties["secret"] = WEIGHTS["secret"] * max(s["score"] for s in secrets)

    if blocking_pii or hard_secrets:
        v.deterministic_violation = True
        v.needs_redaction = True
        v.redact_entity_types = sorted(
            {h["entity_type"] for h in blocking_pii} | {s["secret_type"] for s in hard_secrets}
        )
        v.driving_signal = "secret" if hard_secrets else "pii"
        for h in blocking_pii:
            v.reasons.append(
                f"{h['entity_type']} detected at chars {h['start']}-{h['end']} "
                f"(confidence {h['score']}"
                + (f", validated by {h['validator']}" if h.get("validator") else "")
                + f"); this use case blocks {h['entity_type']}"
            )
        for s in hard_secrets:
            v.reasons.append(
                f"{s['secret_type']} credential detected at chars {s['start']}-{s['end']} "
                f"({s['method']})"
            )

    # ------------------------------------------------------- 2. mechanical
    arithmetic_failed = schema_result.get("arithmetic_failed", 0)
    if arithmetic_failed:
        penalties["arithmetic"] = WEIGHTS["arithmetic"] * min(1.0, arithmetic_failed / 2)
        for a in schema_result.get("arithmetic", []):
            if not a["correct"]:
                v.repair_notes.append(a["message"])
                v.reasons.append(f"arithmetic error: {a['message']}")
    schema = schema_result.get("schema", {})
    if schema.get("applicable") and not schema.get("valid"):
        penalties["schema"] = WEIGHTS["schema"]
        for e in schema.get("errors", []):
            v.reasons.append(f"schema violation: {e}")

    # -------------------------------------------------- 3. probabilistic
    g_score = grounding.get("score")
    g_status = grounding.get("status")
    if g_score is None:
        # No ground truth available. Not a pass and not a failure -- a known
        # unknown, weighted by how much this use case tolerates one.
        factor = {"very_low": 1.6, "low": 1.3, "medium": 1.0, "high": 0.6}.get(risk_tolerance, 1.0)
        penalties["ungroundable"] = WEIGHTS["ungroundable"] * factor
        v.reasons.append(
            "no source documents supplied: the answer's factual claims could not "
            "be verified against anything"
        )
    else:
        if g_score < grounding_threshold:
            penalties["grounding"] = WEIGHTS["grounding"] * (1 - g_score)
            n_bad = grounding.get("unsupported", 0)
            v.reasons.append(
                f"grounding coverage {g_score:.2f} is below this policy's "
                f"threshold of {grounding_threshold:.2f} "
                f"({n_bad} of {grounding.get('n_claims', 0)} claims unsupported)"
            )
        if grounding.get("contradicted"):
            penalties["grounding"] = max(
                penalties.get("grounding", 0.0), WEIGHTS["grounding"] * 0.9
            )
            for c in grounding.get("claims", []):
                for issue in c.get("issues", []):
                    if "contradicts source" in issue:
                        v.reasons.append(f"claim {issue}")

    u_score = uncertainty.get("score", 0.0)
    if u_score > uncertainty_threshold:
        penalties["uncertainty"] = WEIGHTS["uncertainty"] * u_score
        v.reasons.append(
            f"model uncertainty {u_score:.2f} exceeds this policy's threshold of "
            f"{uncertainty_threshold:.2f} (method: {uncertainty.get('method')})"
        )

    # "Confidently wrong": assertive language plus weak evidence. This pairing
    # is worse than an answer that admits doubt, so it carries its own penalty.
    if (
        uncertainty.get("assertiveness", 0.5) >= 0.62
        and g_score is not None
        and g_score < grounding_threshold
    ):
        penalties["grounding"] = penalties.get("grounding", 0.0) + 0.08
        v.reasons.append(
            "confidently wrong pattern: highly assertive language with weak "
            "supporting evidence"
        )

    # ------------------------------------------------- 4. compounding risk
    if conversation_risk > 0.4:
        penalties["conversation"] = min(0.15, conversation_risk * 0.2)
        v.reasons.append(
            f"session risk carried forward: earlier turns in this conversation were "
            f"flagged (accumulated risk {conversation_risk:.2f})"
        )

    v.penalties = penalties
    v.confidence = max(0.0, min(1.0, 1.0 - sum(penalties.values())))

    # --------------------------------------------------- 5. action matrix
    irreversible = (not is_reversible) or downstream_action in IRREVERSIBLE_ACTIONS

    if v.deterministic_violation:
        v.action = "block"
    elif v.confidence < confidence_block:
        v.action = "gate" if irreversible else "flag"
        v.driving_signal = _dominant(penalties)
        v.reasons.append(
            f"aggregate confidence {v.confidence:.2f} is below this policy's floor "
            f"of {confidence_block:.2f}"
        )
    elif arithmetic_failed or (schema.get("applicable") and not schema.get("valid")):
        # Mechanical and provably wrong: annotate with the correct value. We
        # attach the repair, we never silently swap the number in the text.
        v.action = "gate" if irreversible else "edit"
        v.driving_signal = "arithmetic"
    elif penalties.get("grounding") or penalties.get("uncertainty"):
        v.action = "gate" if irreversible else "flag"
        v.driving_signal = _dominant(penalties)
    elif penalties.get("ungroundable"):
        # "We had nothing to check this against" is the normal state of an
        # internal brainstorm and a serious problem for a customer-facing or
        # regulated answer. Flagging every ungroundable response would bury
        # reviewers in alerts about answers that were never verifiable in the
        # first place -- the fastest route to people ignoring the system. So it
        # always shows up in the record, and only forces a flag where this
        # policy's risk appetite says it should.
        if irreversible or risk_tolerance in ("low", "very_low"):
            v.action = "gate" if irreversible else "flag"
            v.driving_signal = "grounding"
        else:
            v.action = "allow"
            v.driving_signal = "none"
            v.reasons.append(
                "delivered with the caveat above: unverifiable is recorded, not escalated, "
                "for a use case with this risk appetite"
            )
    else:
        v.action = "allow"
        v.driving_signal = "none"

    v.gate_required = irreversible and v.action in ("gate", "flag", "edit", "block")

    # ------------------------------------------------ 6. Ring 1 candidacy
    v.ring1_recommended = v.action in ("flag", "edit", "gate")
    v.ring1_priority = _priority(v, irreversible, risk_tolerance)

    if v.action == "allow" and not v.reasons:
        v.reasons.append("all Ring 0 checks passed within this policy's thresholds")

    if cost and cost.get("flags"):
        # Cost findings ride along on the verdict but never change the action.
        v.reasons.extend(f"cost: {f}" for f in cost["flags"])

    return v.to_dict()


def _dominant(penalties: dict) -> str:
    if not penalties:
        return "none"
    key = max(penalties, key=lambda k: penalties[k])
    return {"ungroundable": "grounding"}.get(key, key)


def _priority(v: Verdict, irreversible: bool, risk_tolerance: str) -> float:
    """Ring 1 has a fixed budget, so queued work is ranked, not just queued."""
    p = 1.0 - v.confidence
    if irreversible:
        p += 0.45
    p += {"very_low": 0.25, "low": 0.15, "medium": 0.0, "high": -0.1}.get(risk_tolerance, 0.0)
    if v.deterministic_violation:
        p += 0.2
    return max(0.0, min(2.0, p))


ACTION_EXPLANATIONS = {
    "allow": "Delivered unchanged. Every check passed inside this policy's thresholds.",
    "edit": "Delivered with a correction attached. Only mechanical errors are repaired; "
            "the substance of the answer is never rewritten.",
    "flag": "Delivered in full, with the uncertain claim annotated and the response queued "
            "for review. A probabilistic finding never blocks a reversible answer.",
    "gate": "The text was delivered, but the downstream action it would trigger is held "
            "until a deep check or a human clears it. The answer never waits; the commit does.",
    "block": "Withheld and redacted. A deterministic violation -- personal data or a "
             "credential -- was found, which is a certainty rather than a probability.",
}

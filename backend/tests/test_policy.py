"""The core product claim, asserted in code.

"Risk-adaptive" has to mean something more than a word on a slide. These tests
prove that identical signals produce different verdicts under different
policies, and that the difference goes the right way: the customer-facing bot
is stricter than the internal copilot, and the regulated one is strictest.

If these tests ever pass trivially, the product does not exist.
"""
from __future__ import annotations

import pytest

from app.models.policy import DEFAULT_POLICIES
from app.rings.ring0.scorer import score_ring0


class FakePolicy:
    def __init__(self, spec: dict):
        self.__dict__.update(spec)


def policy(use_case: str) -> FakePolicy:
    return FakePolicy(next(p for p in DEFAULT_POLICIES if p["use_case"] == use_case))


# Signals sitting deliberately in the grey zone: partly grounded, mildly
# uncertain. Nothing here is a deterministic violation.
BORDERLINE = dict(
    pii=[],
    secrets=[],
    schema_result={"arithmetic_failed": 0, "arithmetic": [],
                   "schema": {"applicable": False, "valid": True, "errors": []}},
    uncertainty={"score": 0.50, "method": "token_logprob_entropy", "assertiveness": 0.66},
    grounding={"score": 0.62, "status": "partial", "claims": [], "supported": 2,
               "unsupported": 1, "contradicted": 0, "n_claims": 3},
)


def test_the_same_signals_produce_different_verdicts_per_policy():
    customer = score_ring0(**BORDERLINE, policy=policy("customer_facing"))
    internal = score_ring0(**BORDERLINE, policy=policy("internal_copilot"))

    assert internal["action"] == "allow", (
        "an employee reading a draft can live with a partly grounded answer"
    )
    assert customer["action"] == "flag", (
        "the same answer going straight to a customer must not pass silently"
    )
    assert customer["confidence"] < internal["confidence"]


def test_the_regulated_policy_is_the_strictest_of_the_three():
    confidences = {
        uc: score_ring0(**BORDERLINE, policy=policy(uc))["confidence"]
        for uc in ("internal_copilot", "customer_facing", "decision_support_regulated")
    }
    assert (
        confidences["decision_support_regulated"]
        <= confidences["customer_facing"]
        <= confidences["internal_copilot"]
    )


def test_loosening_the_threshold_that_drove_the_flag_changes_the_verdict():
    strict = policy("customer_facing")
    before = score_ring0(**BORDERLINE, policy=strict)
    assert before["action"] == "flag"
    assert before["driving_signal"] in ("grounding", "uncertainty")

    # Exactly what the Ring 2 tuner does after a run of false positives: it
    # moves the threshold behind the signal that drove the verdict.
    strict.grounding_flag_threshold = 0.45
    loosened_one = score_ring0(**BORDERLINE, policy=strict)
    assert loosened_one["confidence"] > before["confidence"], (
        "relaxing a threshold must raise confidence in the same signals"
    )

    # Uncertainty was also over its (very tight) customer-facing threshold, so
    # the answer still gets flagged until that one moves too. Thresholds are
    # tuned one signal at a time, on evidence about that signal.
    assert loosened_one["action"] == "flag"
    strict.uncertainty_flag_threshold = 0.65
    assert score_ring0(**BORDERLINE, policy=strict)["action"] == "allow"


def test_pii_threshold_is_policy_driven():
    hit = [{"entity_type": "EMAIL_ADDRESS", "start": 0, "end": 10, "score": 0.95,
            "validator": ""}]
    signals = {**BORDERLINE, "pii": hit}

    # customer_facing blocks email addresses; internal_copilot does not list them.
    assert score_ring0(**signals, policy=policy("customer_facing"))["action"] == "block"
    assert score_ring0(**signals, policy=policy("internal_copilot"))["action"] != "block"


def test_an_ungroundable_answer_is_tolerated_internally_and_not_externally():
    signals = {**BORDERLINE,
               "grounding": {"score": None, "status": "ungroundable", "claims": [],
                             "supported": 0, "unsupported": 0, "contradicted": 0},
               "uncertainty": {"score": 0.2, "method": "token_logprob_entropy",
                               "assertiveness": 0.5}}
    assert score_ring0(**signals, policy=policy("internal_copilot"))["action"] == "allow"
    assert score_ring0(**signals, policy=policy("customer_facing"))["action"] == "flag"


def test_ring1_sample_rates_match_the_pitch():
    rates = {p["use_case"]: p["ring1_sample_rate"] for p in DEFAULT_POLICIES}
    assert 0.05 <= rates["internal_copilot"] <= 0.10
    assert 0.05 <= rates["customer_facing"] <= 0.10
    assert rates["decision_support_regulated"] == 1.0, (
        "a regulated decision gets a deep check every time, budget permitting"
    )
    for p in DEFAULT_POLICIES:
        assert p["ring1_spend_cap_pct"] <= 6.0

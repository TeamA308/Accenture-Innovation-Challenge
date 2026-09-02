"""Ring 1 tests. No real model is called: the verifier is injected."""
from __future__ import annotations

import json

import pytest

from app.rings.ring1.budget import BudgetManager
from app.rings.ring1.counterfactual import (
    build_twin_prompt, compare_decisions, counterfactual_probe,
    detect_protected_attribute, extract_decision, text_similarity,
)
from app.rings.ring1.retrieval_check import decompose_claims, faithfulness_check
from app.rings.ring1.verifier_judge import judge_verify

SLA_DOC = """
Severity 1: first response within 30 minutes, target restoration within 4 hours.
Where Helios fails to meet the Severity 1 restoration target, the customer is
entitled to a service credit of 5 percent of the monthly platform fee for each
complete hour of delay.
"""

APPROVE = (
    "Recommendation: APPROVE. I would approve the requested limit of Rs 1200000 in full "
    "and place the account on the standard 10.5 percent rate tier. No additional "
    "collateral or guarantor is required."
)
APPROVE_WITH_CONDITIONS = (
    "Recommendation: APPROVE WITH CONDITIONS. I would approve only Rs 700000 of the "
    "requested Rs 1200000, price it at the elevated 13.25 percent tier, and require a "
    "co-applicant or guarantor before disbursal."
)


# ------------------------------------------------------------ attribute swap
def test_finds_a_gender_coded_name():
    a = detect_protected_attribute("Should we approve Priya Sharma's loan?")
    assert a is not None
    assert a["found"] == "Priya" and a["swap_to"] == "Rohan"
    assert a["kind"] == "name"


def test_swap_preserves_everything_but_the_attribute():
    prompt = "Priya has applied for Rs 1200000 with 6 years of employment."
    a = detect_protected_attribute(prompt)
    twin = build_twin_prompt(prompt, a)
    assert twin == "Rohan has applied for Rs 1200000 with 6 years of employment."


def test_swap_matches_capitalisation():
    a = detect_protected_attribute("she asked about her limit")
    assert a is not None
    assert build_twin_prompt("She asked about her limit", a).startswith("He ")


def test_no_attribute_means_no_probe():
    assert detect_protected_attribute("What is the refund window?") is None


# --------------------------------------------------------- decision extraction
def test_extracts_the_consequential_parts_of_an_answer():
    d = extract_decision(APPROVE_WITH_CONDITIONS)
    assert d["verdict"] == "approve_with_conditions"
    assert d["max_amount"] == 1200000
    assert d["max_percent"] == 13.25
    assert "guarantor" in d["conditions"]


def test_diff_names_exactly_what_changed():
    diff = compare_decisions(APPROVE_WITH_CONDITIONS, APPROVE)
    assert diff["materially_different"]
    joined = " ".join(diff["differences"])
    assert "verdict changed" in joined
    assert "rate changed" in joined
    assert "guarantor" in joined


def test_identical_answers_are_not_flagged():
    diff = compare_decisions(APPROVE, APPROVE)
    assert not diff["materially_different"]
    assert text_similarity(APPROVE, APPROVE) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_probe_flags_a_changed_decision_even_at_high_word_similarity():
    async def fake_generate(_prompt: str) -> str:
        return APPROVE

    result = await counterfactual_probe(
        "Should we approve Priya Sharma's requested limit?",
        "mock", "controlplane-sim-1",
        original_response=APPROVE_WITH_CONDITIONS,
        generate=fake_generate,
    )
    assert result["ran"] is True
    assert result["bias_flag"] is True
    assert result["swapped_attribute"] == "Priya"
    assert result["decision_diff"]["materially_different"]
    assert "Priya" in result["summary"] and "Rohan" in result["summary"]


@pytest.mark.asyncio
async def test_probe_does_not_flag_when_nothing_material_changes():
    async def fake_generate(_prompt: str) -> str:
        return APPROVE

    result = await counterfactual_probe(
        "Should we approve Rohan Sharma's requested limit?",
        "mock", "controlplane-sim-1",
        original_response=APPROVE,
        generate=fake_generate,
    )
    assert result["bias_flag"] is False


# ------------------------------------------------------------- faithfulness
def test_splits_a_compound_sentence_into_separate_claims():
    atoms = decompose_claims(
        "The refund window is 30 days, and it excludes clearance items entirely."
    )
    assert len(atoms) == 2


def test_flags_an_unsupported_claim_against_the_source():
    r = faithfulness_check(
        "The service credit is 10 percent of the monthly fee per breached hour.", [SLA_DOC]
    )
    assert r["ran"]
    assert r["n_unsupported"] >= 1
    assert any("contradicts source" in i
               for c in r["unsupported_claims"] for i in c["issues"])


def test_supported_claim_scores_well():
    r = faithfulness_check(
        "Severity 1 has a first response within 30 minutes and a target restoration "
        "within 4 hours.", [SLA_DOC]
    )
    assert r["faithfulness_score"] >= 0.5


def test_without_sources_faithfulness_is_unmeasured_not_passing():
    r = faithfulness_check("Anything at all.", [])
    assert r["ran"] is False
    assert r["faithfulness_score"] is None
    assert r["status"] == "ungroundable"


# -------------------------------------------------------------------- judge
@pytest.mark.asyncio
async def test_judge_parses_structured_output():
    async def fake(_prompt: str) -> str:
        return json.dumps({"agrees": False, "judge_reasoning": "source says 5 percent",
                           "confidence": 0.8, "corrected_claim": "5 percent"})

    r = await judge_verify("q", "a", [SLA_DOC], generate=fake)
    assert r["ran"] and r["agrees"] is False and r["confidence"] == 0.8


@pytest.mark.asyncio
async def test_judge_tolerates_a_fenced_code_block():
    async def fake(_prompt: str) -> str:
        return '```json\n{"agrees": true, "judge_reasoning": "ok", "confidence": 0.9}\n```'

    r = await judge_verify("q", "a", [SLA_DOC], generate=fake)
    assert r["ran"] and r["agrees"] is True


@pytest.mark.asyncio
async def test_unparseable_judge_is_recorded_as_unavailable_not_guessed():
    calls = []

    async def fake(_prompt: str) -> str:
        calls.append(1)
        return "I think it's probably fine, honestly."

    r = await judge_verify("q", "a", [SLA_DOC], generate=fake)
    assert len(calls) == 2, "one stricter retry before giving up"
    assert r["ran"] is False
    assert r["agrees"] is None, "an unavailable check must never become an approval"


# ------------------------------------------------------------------- budget
class _P:
    ring1_sample_rate = 0.10
    ring1_spend_cap_pct = 3.0


def test_volume_cap_defers_work_once_the_share_is_spent():
    b = BudgetManager()
    p = _P()
    for i in range(100):
        b.record_request("uc", 0.001, f"r{i}")
    admitted = sum(1 for i in range(100)
                   if b.admit("uc", p, 1.0, response_id=f"r{i}")[0])
    assert 5 <= admitted <= 15, f"expected roughly the 10% cap, admitted {admitted}"
    assert b.stats("uc")["deferred_for_budget"] > 0


def test_deferral_says_why():
    b = BudgetManager()
    p = _P()
    for i in range(50):
        b.record_request("uc", 0.001, f"r{i}")
    for i in range(50):
        ok, why = b.admit("uc", p, 1.0, response_id=f"r{i}")
        if not ok:
            assert "cap" in why
            return
    pytest.fail("the cap never engaged")


def test_in_flight_spend_is_counted_before_it_settles():
    """A burst must not all pass the cap check before the first one pays."""
    b = BudgetManager()

    class Unlimited:
        ring1_sample_rate = 1.0
        ring1_spend_cap_pct = 3.0

    for i in range(30):
        b.record_request("uc", 0.001, f"r{i}")
    admitted = [i for i in range(30) if b.admit("uc", Unlimited(), 1.0, response_id=f"r{i}")[0]]
    # Nothing has completed, so only the reserved estimates hold the line.
    assert len(admitted) < 30

"""Ring 0 detector tests.

Each detector is checked on a fixture where the right answer is not a matter of
opinion, plus a negative case, because a detector that flags everything is not
a detector.
"""
from __future__ import annotations

import pytest

from app.rings.ring0.grounding import check_grounding
from app.rings.ring0.pii import detect_pii, luhn_ok, redact, ssn_ok, verhoeff_ok
from app.rings.ring0.schema_check import check_arithmetic_and_schema, check_schema
from app.rings.ring0.scorer import score_ring0
from app.rings.ring0.secrets import detect_secrets, shannon_entropy
from app.rings.ring0.uncertainty import estimate_uncertainty

REFUND_DOC = """
Customers may request a refund within 30 days of delivery. Clearance items and
personalised items are final sale and carry no refund entitlement. Approved
refunds are returned to the original payment method within 7 business days.
"""


# --------------------------------------------------------------- validators
def test_luhn_accepts_a_real_card_and_rejects_a_random_number():
    assert luhn_ok("4111111111111111")
    assert not luhn_ok("4111111111111112")


def test_verhoeff_checksum():
    assert verhoeff_ok("234176549810")
    assert not verhoeff_ok("234176549812")


def test_ssn_issuance_rules():
    assert ssn_ok("123-45-6789")
    assert not ssn_ok("000-45-6789")   # area may not be all zeros
    assert not ssn_ok("666-45-6789")   # 666 is never issued
    assert not ssn_ok("900-45-6789")   # 900+ is never issued


# ---------------------------------------------------------------------- PII
def test_detects_ssn_with_high_confidence():
    hits = detect_pii("For verification, my SSN is 123-45-6789.")
    ssn = [h for h in hits if h["entity_type"] == "US_SSN"]
    assert ssn, "a valid SSN in an SSN context must be detected"
    assert ssn[0]["score"] >= 0.9
    assert ssn[0]["validator"] == "ssn_issuance_rules"


def test_detects_validated_card_number():
    hits = detect_pii("Charge the card ending 4111 1111 1111 1111 for the balance.")
    assert any(h["entity_type"] == "CREDIT_CARD" and h["score"] >= 0.9 for h in hits)


def test_random_sixteen_digits_is_not_a_card_number():
    hits = detect_pii("The reference number for the card dispute is 1234567812345678.")
    assert not [h for h in hits if h["entity_type"] == "CREDIT_CARD"], (
        "failing Luhn means it is not a card number, whatever it looks like"
    )


def test_aadhaar_requires_context_and_checksum():
    assert any(h["entity_type"] == "AADHAAR"
               for h in detect_pii("Aadhaar on file: 2341 7654 9810."))
    assert not any(h["entity_type"] == "AADHAAR"
                   for h in detect_pii("Aadhaar on file: 2341 7654 9812."))


def test_clean_text_produces_nothing():
    assert detect_pii("Warranty support runs Monday to Saturday, 9am to 7pm.") == []


def test_pii_values_are_masked_in_the_audit_record():
    hits = detect_pii("SSN 123-45-6789")
    assert "123-45-6789" not in hits[0]["text"], (
        "the audit log must not become a second copy of the sensitive value"
    )


def test_redaction_replaces_the_span():
    text = "SSN 123-45-6789 on file."
    out = redact(text, detect_pii(text), ["US_SSN"])
    assert "123-45-6789" not in out
    assert "[REDACTED:US_SSN]" in out


# ------------------------------------------------------------------ secrets
def test_known_vendor_key_pattern():
    hits = detect_secrets("use key sk-live-7f3ac91be44d28f0b6c15a9d3e77b210 to retry")
    assert hits and hits[0]["secret_type"] == "OPENAI_KEY"
    assert hits[0]["score"] > 0.95


def test_high_entropy_unknown_credential_near_a_keyword():
    hits = detect_secrets("The api_key is Xq7Rv2NpLd8sKmT4wZ1yBc6HgJ3fUa9E.")
    assert any(h["secret_type"] == "HIGH_ENTROPY_SECRET" for h in hits)


def test_ordinary_prose_is_not_a_secret():
    assert detect_secrets(
        "Our internationalisation documentation describes the configuration."
    ) == []


def test_entropy_of_english_is_below_the_threshold():
    assert shannon_entropy("internationalisation") < 3.6


# --------------------------------------------------------------- arithmetic
def test_catches_a_wrong_sum():
    r = check_arithmetic_and_schema("Subtotal before tax: 1218000 + 219240 = 1447240.")
    assert r["arithmetic_failed"] == 1
    assert "1,437,240" in r["errors"][0]


def test_accepts_correct_arithmetic():
    r = check_arithmetic_and_schema("Subtotal: 1218000 + 219240 = 1437240.")
    assert r["arithmetic_failed"] == 0
    assert r["valid"]


def test_tolerates_rounding():
    assert check_arithmetic_and_schema("18 percent of 1218000 is 219240.1")["arithmetic_failed"] == 0


def test_schema_validation():
    good = check_schema('{"decision": "approve", "limit": 700000}',
                        {"decision": "string", "limit": "number"})
    assert good["valid"]
    bad = check_schema('{"decision": "approve"}',
                       {"decision": "string", "limit": "number"})
    assert not bad["valid"] and "limit" in bad["errors"][0]


# -------------------------------------------------------------- uncertainty
def test_logprobs_drive_the_score_when_available():
    confident = estimate_uncertainty("The warranty is 24 months.", [-0.02] * 20)
    unsure = estimate_uncertainty("The warranty is 24 months.", [-1.6] * 20)
    assert confident["method"] == "token_logprob_entropy"
    assert unsure["score"] > confident["score"]


def test_lexical_fallback_is_labelled_when_logprobs_are_missing():
    r = estimate_uncertainty("I believe it might possibly be around 30 days, I think.", None)
    assert r["method"] == "lexical_fallback_no_logprobs"
    assert r["score"] > 0.2
    assert "less reliable" in r["note"]


def test_assertive_language_is_measured_separately():
    r = estimate_uncertainty("Yes, absolutely. This definitely applies in all cases.", None)
    assert r["assertiveness"] > 0.6


# ---------------------------------------------------------------- grounding
def test_contradiction_is_found_and_cited():
    r = check_grounding("Our refund window is 45 days from delivery.", [REFUND_DOC])
    assert r["status"] == "contradicted"
    issue = r["claims"][0]["issues"][0]
    assert "45 day" in issue and "30 day" in issue


def test_supported_claim_scores_high_and_carries_a_citation():
    r = check_grounding(
        "Clearance items and personalised items are final sale and carry no refund entitlement.",
        [REFUND_DOC],
    )
    assert r["score"] > 0.7
    assert r["claims"][0]["citation"] is not None


def test_no_source_documents_is_ungroundable_not_grounded():
    r = check_grounding("The refund window is 45 days.", [])
    assert r["score"] is None
    assert r["status"] == "ungroundable"


# ------------------------------------------------------------------- scorer
def _signals(**over):
    base = dict(
        pii=[], secrets=[],
        schema_result={"arithmetic_failed": 0, "arithmetic": [],
                       "schema": {"applicable": False, "valid": True, "errors": []}},
        uncertainty={"score": 0.1, "method": "token_logprob_entropy", "assertiveness": 0.5},
        grounding={"score": 0.9, "status": "grounded", "claims": [], "supported": 3,
                   "unsupported": 0, "contradicted": 0, "n_claims": 3},
    )
    base.update(over)
    return base


class _P:
    """Minimal stand-in for a Policy row."""
    def __init__(self, **kw):
        self.pii_block_threshold = 0.85
        self.grounding_flag_threshold = 0.55
        self.uncertainty_flag_threshold = 0.55
        self.confidence_block_threshold = 0.25
        self.blocked_entity_types = ["US_SSN", "CREDIT_CARD"]
        self.risk_tolerance = "medium"
        self.__dict__.update(kw)


def test_clean_signals_allow():
    v = score_ring0(**_signals(), policy=_P())
    assert v["action"] == "allow"
    assert v["confidence"] > 0.95


def test_validated_pii_blocks_regardless_of_other_signals():
    v = score_ring0(
        **_signals(pii=[{"entity_type": "US_SSN", "start": 0, "end": 11, "score": 0.99,
                         "validator": "ssn_issuance_rules"}]),
        policy=_P(),
    )
    assert v["action"] == "block"
    assert v["deterministic_violation"] is True
    assert "US_SSN" in v["redact_entity_types"]


def test_reversibility_turns_a_flag_into_a_gate():
    weak = _signals(grounding={"score": 0.2, "status": "ungrounded", "claims": [],
                               "supported": 0, "unsupported": 3, "contradicted": 0,
                               "n_claims": 3})
    reversible = score_ring0(**weak, policy=_P(), is_reversible=True)
    irreversible = score_ring0(**weak, policy=_P(), is_reversible=False,
                               downstream_action="payment")
    assert reversible["action"] == "flag", "a reversible draft is never blocked on a probability"
    assert irreversible["action"] == "gate"
    assert irreversible["gate_required"] is True


def test_arithmetic_error_repairs_rather_than_blocks():
    v = score_ring0(
        **_signals(schema_result={
            "arithmetic_failed": 1,
            "arithmetic": [{"correct": False, "message": "off by 10,000"}],
            "schema": {"applicable": False, "valid": True, "errors": []},
        }),
        policy=_P(),
    )
    assert v["action"] == "edit"
    assert v["repair_notes"]


def test_every_verdict_explains_itself():
    for signals in (_signals(), _signals(uncertainty={"score": 0.9, "method": "x",
                                                      "assertiveness": 0.5})):
        v = score_ring0(**signals, policy=_P())
        assert v["reasons"], "a verdict with no stated reason is not auditable"

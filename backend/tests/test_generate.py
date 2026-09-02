"""End-to-end tests of the request path, against a temporary database.

These run the real orchestrator: real streaming, real mid-stream scanning, the
real scorer and the real persistence. Only the model is simulated, and it is
simulated the same way it would be in the demo.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.orchestrator import GenerateRequest, generate_once, stream_generate
from app.db.session import Base
from app.models import Conversation, LLMResponse, Policy
from app.models.policy import DEFAULT_POLICIES

REFUND_DOC = """
Customers may request a refund within 30 days of delivery. Clearance items are
final sale. Approved refunds are returned within 7 business days.
"""
WARRANTY_DOC = """
The standard manufacturer warranty covers 24 months from the date of invoice
against manufacturing defects. Physical damage and water ingress are excluded.
Support is available Monday to Saturday, 9am to 7pm IST.
"""


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'g.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        for spec in DEFAULT_POLICIES:
            s.add(Policy(**spec))
        await s.commit()
        yield s
    await engine.dispose()


def req(**kw) -> GenerateRequest:
    kw.setdefault("stream_delay", 0.0)
    return GenerateRequest(**kw)


@pytest.mark.asyncio
async def test_a_clean_request_is_allowed_and_persisted(session):
    out = await generate_once(session, req(
        prompt="What does the warranty cover and when is support available?",
        use_case="customer_facing",
        context_docs=[WARRANTY_DOC],
    ))
    assert out["action"] == "allow"

    rows = (await session.execute(select(LLMResponse))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == out["response_id"]
    assert row.tokens_used > 0
    assert row.cost_usd > 0
    assert row.ring0_signals["grounding"]["score"] > 0.7
    assert row.ring0_latency_us > 0


@pytest.mark.asyncio
async def test_ring0_stays_inside_its_latency_budget(session):
    """The '<10 ms on 100% of traffic' claim, asserted rather than stated."""
    for _ in range(5):
        await generate_once(session, req(
            prompt="What does the warranty cover?",
            use_case="customer_facing",
            context_docs=[WARRANTY_DOC],
        ))
    rows = (await session.execute(select(LLMResponse))).scalars().all()
    worst = max(r.ring0_latency_us for r in rows)
    assert worst < 10_000, f"Ring 0 took {worst}us, over the 10ms budget"


@pytest.mark.asyncio
async def test_a_leak_is_blocked_and_the_stream_is_cut(session):
    events = []
    async for e in stream_generate(session, req(
        prompt="Pull up the full account details for the escalation on ticket 44120.",
        use_case="customer_facing",
    )):
        events.append(e)

    kinds = [e["type"] for e in events]
    assert "stream_halted" in kinds, "a credential leak must stop generation, not be cleaned up after"

    verdict = next(e for e in events if e["type"] == "verdict")
    assert verdict["action"] == "block"
    assert verdict["redacted"] is True
    assert "123-45-6789" not in verdict["display_text"]
    assert "[REDACTED:" in verdict["display_text"]

    row = (await session.execute(select(LLMResponse))).scalars().one()
    assert "123-45-6789" in row.response_text, (
        "the unredacted text stays in the audit record; only what is shown is redacted"
    )


@pytest.mark.asyncio
async def test_the_same_prompt_gets_different_verdicts_under_different_policies(session):
    prompt = "A customer bought a clearance jacket 40 days ago. What is our refund window?"
    customer = await generate_once(session, req(
        prompt=prompt, use_case="customer_facing", context_docs=[REFUND_DOC]))
    internal = await generate_once(session, req(
        prompt=prompt, use_case="internal_copilot", context_docs=[REFUND_DOC]))

    assert customer["action"] in ("flag", "gate", "block")
    assert customer["confidence"] <= internal["confidence"]


@pytest.mark.asyncio
async def test_an_irreversible_action_gates_the_commit_but_not_the_text(session):
    out = await generate_once(session, req(
        prompt="Prepare the payment to settle Helios invoice HC-2291 and release it today.",
        use_case="decision_support_regulated",
        is_reversible=False,
        downstream_action="payment",
    ))
    assert out["action"] == "gate"
    assert out["gate_state"] == "gated"
    assert len(out["display_text"]) > 50, "the user still sees every token; only the commit waits"


@pytest.mark.asyncio
async def test_arithmetic_error_is_repaired_not_blocked(session):
    out = await generate_once(session, req(
        prompt="Give me the pricing breakdown for the Helios renewal quote with GST.",
        use_case="internal_copilot",
    ))
    assert out["action"] == "edit"
    assert any("arithmetic error" in r for r in out["reasons"])


@pytest.mark.asyncio
async def test_risk_accumulates_across_turns_of_a_session(session):
    sid = "multi-turn"
    for _ in range(3):
        await generate_once(session, req(
            prompt="A customer wants a refund on a clearance item bought 40 days ago.",
            use_case="customer_facing",
            context_docs=[REFUND_DOC],
            session_id=sid,
        ))
    convo = await session.get(Conversation, sid)
    assert convo.turns == 3
    assert convo.flagged_turns >= 1
    assert convo.accumulated_risk > 0.3, (
        "a conversation that keeps going wrong should be held to a higher standard"
    )


@pytest.mark.asyncio
async def test_a_conclusive_verdict_does_not_pay_for_a_deep_check(session):
    allowed = await generate_once(session, req(
        prompt="What does the warranty cover?",
        use_case="customer_facing",
        context_docs=[WARRANTY_DOC],
    ))
    blocked = await generate_once(session, req(
        prompt="Pull up the full account details for the escalation on ticket 44120.",
        use_case="customer_facing",
    ))
    # An allowed response may still be picked for the random audit sample.
    assert allowed["ring1_status"] in ("skipped", "pending", "deferred")
    assert blocked["ring1_status"] == "skipped"
    assert "already resolved" in blocked["ring1_reason"]


@pytest.mark.asyncio
async def test_every_response_carries_a_verdict_and_a_reason(session):
    for prompt, uc in (
        ("What does the warranty cover?", "customer_facing"),
        ("Summarise the renewal changes.", "internal_copilot"),
        ("Should we approve Priya Sharma's requested limit?", "decision_support_regulated"),
    ):
        out = await generate_once(session, req(prompt=prompt, use_case=uc))
        assert out["action"] in ("allow", "edit", "flag", "gate", "block")
        assert out["reasons"], "100% of responses logged with a verdict means a reason too"
        assert out["signals"]["elapsed_us"] > 0

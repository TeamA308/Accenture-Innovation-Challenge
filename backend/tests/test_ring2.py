"""Ring 2 tests: the learning loop, against a real (temporary) database.

The claim being tested is the one that is easiest to fake in a demo and hardest
to fake in code: human overrides actually move the thresholds, the change is
logged with a readable reason, and the things that should never move do not.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.session import Base
from app.models import LLMResponse, Override, Policy, ThresholdAdjustment
from app.models.policy import DEFAULT_POLICIES
from app.rings.ring2.threshold_tuner import FP_TRIGGER, WINDOW, retune_thresholds
from app.rings.ring2.trust_metrics import trust_report


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        for spec in DEFAULT_POLICIES:
            s.add(Policy(**spec))
        await s.commit()
        yield s
    await engine.dispose()


async def _flagged_response(session, use_case="internal_copilot") -> LLMResponse:
    row = LLMResponse(
        prompt="What is the service credit cap?",
        use_case=use_case,
        model_provider="mock",
        model_name="controlplane-sim-1",
        response_text="It is capped somewhere around 30 percent.",
        confidence=0.5,
        action="flag",
        final_action="flag",
        ring0_signals={"grounding": {"score": 0.3, "status": "partial"},
                       "uncertainty": {"score": 0.4}},
    )
    session.add(row)
    await session.flush()
    return row


async def _override(session, row, decision, signal="grounding", machine_action="flag"):
    o = Override(
        response_id=row.id,
        use_case=row.use_case,
        decision=decision,
        driving_signal=signal,
        machine_action=machine_action,
    )
    session.add(o)
    await session.flush()
    return o


async def _policy(session, use_case="internal_copilot") -> Policy:
    return (await session.execute(
        select(Policy).where(Policy.use_case == use_case)
    )).scalar_one()


# ---------------------------------------------------------------- the loop
@pytest.mark.asyncio
async def test_a_run_of_false_positives_loosens_the_threshold(session):
    policy = await _policy(session)
    before = policy.grounding_flag_threshold

    adjustments = []
    for i in range(WINDOW):
        row = await _flagged_response(session)
        o = await _override(session, row, "accept")   # reviewer: we over-flagged
        adjustments = await retune_thresholds(session, policy, o, row)
        await session.commit()
        if i < WINDOW - 1:
            assert adjustments == [], (
                "the tuner must not move on thin evidence -- it needs a run, not one opinion"
            )

    assert len(adjustments) == 1, "the fifth override completes the evidence window"
    assert policy.grounding_flag_threshold < before

    logged = (await session.execute(select(ThresholdAdjustment))).scalars().all()
    assert len(logged) == 1
    assert logged[0].field_changed == "grounding_flag_threshold"
    assert logged[0].old_value == before
    assert logged[0].new_value == policy.grounding_flag_threshold
    assert "false positive" in logged[0].reason
    assert str(int(FP_TRIGGER * 100)) in logged[0].reason


@pytest.mark.asyncio
async def test_agreeing_with_the_flag_changes_nothing(session):
    policy = await _policy(session)
    before = policy.grounding_flag_threshold
    for _ in range(WINDOW + 2):
        row = await _flagged_response(session)
        o = await _override(session, row, "reject")   # reviewer: the flag was right
        assert await retune_thresholds(session, policy, o, row) == []
        await session.commit()
    assert policy.grounding_flag_threshold == before


@pytest.mark.asyncio
async def test_a_single_miss_tightens_immediately(session):
    """A false negative is not symmetric with a false alarm and is not treated as one."""
    policy = await _policy(session)
    before = policy.grounding_flag_threshold

    row = await _flagged_response(session)
    row.action = row.final_action = "allow"
    o = await _override(session, row, "reject", machine_action="allow")
    adjustments = await retune_thresholds(session, policy, o, row)
    await session.commit()

    assert len(adjustments) == 1
    assert policy.grounding_flag_threshold > before, "a miss tightens, and does so at once"
    assert "missed detection" in adjustments[0].reason


@pytest.mark.asyncio
async def test_deterministic_checks_are_never_tuned_away(session):
    """No volume of reviewer disagreement relaxes a validated identity match."""
    policy = await _policy(session)
    before = policy.pii_block_threshold

    for _ in range(WINDOW + 3):
        row = await _flagged_response(session)
        row.action = row.final_action = "block"
        o = await _override(session, row, "accept", signal="pii", machine_action="block")
        assert await retune_thresholds(session, policy, o, row) == []
        await session.commit()

    assert policy.pii_block_threshold == before
    assert (await session.execute(select(ThresholdAdjustment))).scalars().all() == []


@pytest.mark.asyncio
async def test_thresholds_cannot_be_driven_out_of_bounds(session):
    policy = await _policy(session)
    policy.grounding_flag_threshold = 0.22
    for _ in range(WINDOW * 4):
        row = await _flagged_response(session)
        o = await _override(session, row, "accept")
        await retune_thresholds(session, policy, o, row)
        await session.commit()
    assert policy.grounding_flag_threshold >= 0.20


@pytest.mark.asyncio
async def test_policies_are_tuned_independently(session):
    internal = await _policy(session, "internal_copilot")
    customer = await _policy(session, "customer_facing")
    customer_before = customer.grounding_flag_threshold

    for _ in range(WINDOW):
        row = await _flagged_response(session, "internal_copilot")
        o = await _override(session, row, "accept")
        await retune_thresholds(session, internal, o, row)
        await session.commit()

    assert customer.grounding_flag_threshold == customer_before, (
        "one team's alert fatigue must not loosen another team's controls"
    )


# ------------------------------------------------------------ trust report
@pytest.mark.asyncio
async def test_trust_report_counts_the_confusion_matrix(session):
    for decision, machine in (("reject", "flag"), ("reject", "flag"),
                              ("accept", "flag"), ("accept", "allow")):
        row = await _flagged_response(session)
        row.final_action = machine
        await _override(session, row, decision, machine_action=machine)
    await session.commit()

    report = await trust_report(session)
    cm = report["confusion_matrix"]
    assert cm["true_positive"] == 2
    assert cm["false_positive"] == 1
    assert cm["true_negative"] == 1
    assert report["precision"] == pytest.approx(2 / 3, abs=0.01)


@pytest.mark.asyncio
async def test_trust_report_admits_when_the_sample_is_too_small(session):
    row = await _flagged_response(session)
    await _override(session, row, "accept")
    await session.commit()

    report = await trust_report(session)
    assert report["sample_is_sufficient"] is False
    assert "indicative" in report["caveat"]

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import bus
from app.db.session import get_session
from app.models import LLMResponse, Override, Policy
from app.rings.ring2.threshold_tuner import NOT_TUNABLE, flag_rate_check, retune_thresholds

router = APIRouter(prefix="/v1/review", tags=["review"])

REVIEWABLE = ("flag", "edit", "gate", "block")


class OverrideBody(BaseModel):
    decision: str  # accept | reject | edit
    edited_text: str | None = None
    notes: str | None = None
    reviewer_id: str = "reviewer@demo"


@router.get("/queue")
async def review_queue(
    use_case: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    """Everything waiting on a human, highest risk first.

    Ordering is by risk, not arrival: an irreversible action that is still
    gated outranks a flagged draft no matter which came in first.
    """
    q = (
        select(LLMResponse)
        .where(LLMResponse.final_action.in_(REVIEWABLE))
        .where(LLMResponse.reviewed.is_(False))
        .order_by(desc(LLMResponse.created_at))
        .limit(limit * 3)
    )
    if use_case:
        q = q.where(LLMResponse.use_case == use_case)
    rows = (await session.execute(q)).scalars().all()

    def risk(r: LLMResponse) -> tuple:
        return (
            0 if r.gate_state == "gated" else 1,
            0 if not r.is_reversible else 1,
            r.confidence,
        )

    items = []
    for r in sorted(rows, key=risk)[:limit]:
        d = r.to_detail()
        d["ring1_result"] = r.ring1_result
        items.append(d)
    return {"count": len(items), "items": items}


@router.post("/{response_id}/override")
async def create_override(
    response_id: str, body: OverrideBody, session: AsyncSession = Depends(get_session)
):
    if body.decision not in ("accept", "reject", "edit"):
        raise HTTPException(400, "decision must be one of accept, reject, edit")

    row = await session.get(LLMResponse, response_id)
    if row is None:
        raise HTTPException(404, "response not found")

    policy = (await session.execute(
        select(Policy).where(Policy.use_case == row.use_case)
    )).scalar_one_or_none()

    signals = row.ring0_signals or {}
    driving = _driving_signal(row, signals)

    override = Override(
        response_id=response_id,
        use_case=row.use_case,
        reviewer_id=body.reviewer_id,
        decision=body.decision,
        edited_text=body.edited_text,
        notes=body.notes,
        driving_signal=driving,
        machine_action=row.final_action,
    )
    session.add(override)

    row.reviewed = True
    if body.decision == "accept":
        # The reviewer says the answer was fine: release the gate.
        row.final_action = "allow"
        row.gate_state = "released" if row.gate_state in ("gated", "withheld") else row.gate_state
    elif body.decision == "reject":
        row.final_action = "block"
        row.gate_state = "withheld" if row.gate_state != "open" else row.gate_state
    else:
        row.final_action = "edit"
        row.gate_state = "released" if row.gate_state in ("gated", "withheld") else row.gate_state
        if body.edited_text:
            row.redacted_text = body.edited_text

    await session.flush()

    adjustments = []
    if policy is not None:
        adjustments = await retune_thresholds(session, policy, override, row)
        if not adjustments:
            # The per-signal rule had nothing to say. Fall back to the slower
            # service-level loop, which watches the reviewer's total workload.
            slo_adj = await flag_rate_check(session, policy)
            if slo_adj is not None:
                adjustments.append(slo_adj)
    await session.commit()

    payload = {
        "override": override.to_dict(),
        "response": row.to_summary(),
        "adjustments": [a.to_dict() for a in adjustments],
        "policy": policy.to_dict() if policy else None,
        "tuner_note": (
            NOT_TUNABLE.get(driving)
            if not adjustments and driving in NOT_TUNABLE
            else ("threshold moved" if adjustments else
                  "recorded; not enough evidence to move a threshold yet")
        ),
    }
    await bus.publish({"type": "override", **payload})
    return payload


def _driving_signal(row: LLMResponse, signals: dict) -> str:
    """Which signal actually caused this verdict.

    Stored on the override so the tuner knows which threshold a human just
    disagreed with. Recomputed from the stored signals rather than trusted from
    the client.
    """
    if row.action == "allow":
        return "none"
    if signals.get("secrets"):
        return "secret"
    if signals.get("pii") and row.action == "block":
        return "pii"
    sa = signals.get("schema_arithmetic", {})
    if sa.get("arithmetic_failed"):
        return "arithmetic"
    ground = signals.get("grounding", {})
    unc = signals.get("uncertainty", {})
    g_penalty = 1.0 - (ground.get("score") if ground.get("score") is not None else 0.5)
    u_penalty = unc.get("score", 0.0)
    if ground.get("status") == "ungroundable":
        return "grounding"
    return "grounding" if g_penalty >= u_penalty else "uncertainty"


@router.get("/stats")
async def review_stats(session: AsyncSession = Depends(get_session)):
    pending = (await session.execute(
        select(LLMResponse)
        .where(LLMResponse.final_action.in_(REVIEWABLE))
        .where(LLMResponse.reviewed.is_(False))
    )).scalars().all()
    overrides = (await session.execute(select(Override))).scalars().all()
    return {
        "pending": len(pending),
        "gated": sum(1 for r in pending if r.gate_state == "gated"),
        "overrides_recorded": len(overrides),
        "by_decision": {
            d: sum(1 for o in overrides if o.decision == d)
            for d in ("accept", "reject", "edit")
        },
    }

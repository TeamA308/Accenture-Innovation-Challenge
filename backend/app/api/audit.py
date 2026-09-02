from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import Conversation, LLMResponse, Override, ThresholdAdjustment
from app.rings.ring0.scorer import ACTION_EXPLANATIONS

router = APIRouter(prefix="/v1/responses", tags=["audit"])


@router.get("")
async def list_responses(
    limit: int = 50,
    use_case: str | None = None,
    action: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    q = select(LLMResponse).order_by(desc(LLMResponse.created_at)).limit(min(limit, 500))
    if use_case:
        q = q.where(LLMResponse.use_case == use_case)
    if action:
        q = q.where(LLMResponse.final_action == action)
    rows = (await session.execute(q)).scalars().all()
    return {"count": len(rows), "items": [r.to_summary() for r in rows]}


@router.get("/{response_id}")
async def response_detail(response_id: str, session: AsyncSession = Depends(get_session)):
    """The evidence drawer.

    Everything needed to reconstruct and defend one decision: the prompt, the
    raw and redacted text, every Ring 0 signal with its spans and validators,
    the Ring 1 result, the human override, and any threshold that moved because
    of it. This is the record a DPDP or EU AI Act reviewer would ask for.
    """
    row = await session.get(LLMResponse, response_id)
    if row is None:
        raise HTTPException(404, "response not found")

    overrides = (await session.execute(
        select(Override).where(Override.response_id == response_id)
        .order_by(Override.created_at)
    )).scalars().all()

    adjustments = (await session.execute(
        select(ThresholdAdjustment)
        .where(ThresholdAdjustment.triggered_by_override_id.in_([o.id for o in overrides] or [""]))
    )).scalars().all()

    convo = await session.get(Conversation, row.session_id)

    detail = row.to_detail()
    detail.update({
        "overrides": [o.to_dict() for o in overrides],
        "threshold_adjustments": [a.to_dict() for a in adjustments],
        "conversation": convo.to_dict() if convo else None,
        "action_explanation": ACTION_EXPLANATIONS.get(row.final_action, ""),
        "evidence_note": (
            "Every field below was produced at request time and stored verbatim. "
            "Signal spans are character offsets into the raw response text."
        ),
    })
    return detail


@router.get("/{response_id}/export")
async def export_evidence(response_id: str, session: AsyncSession = Depends(get_session)):
    """A flat, human-readable evidence pack for a compliance file."""
    detail = await response_detail(response_id, session)
    signals = detail.get("ring0_signals", {})
    lines: list[str] = [
        "CONTROLPLANE.AI - RESPONSE EVIDENCE RECORD",
        f"Response ID : {detail['id']}",
        f"Timestamp   : {detail['created_at']}",
        f"Use case    : {detail['use_case']}",
        f"Model       : {detail['model_provider']}/{detail['model_name']}",
        f"Verdict     : {detail['final_action'].upper()} "
        f"(confidence {detail['confidence']})",
        f"Gate state  : {detail['gate_state']}",
        "",
        "WHY:",
    ]
    lines += [f"  - {r}" for r in detail.get("action_reasons", [])]
    lines += ["", "RING 0 SIGNALS:"]
    for hit in signals.get("pii", []):
        lines.append(
            f"  personal data  {hit['entity_type']} at {hit['start']}-{hit['end']} "
            f"score {hit['score']} validator {hit.get('validator') or 'pattern'}"
        )
    for hit in signals.get("secrets", []):
        lines.append(
            f"  credential     {hit['secret_type']} at {hit['start']}-{hit['end']} "
            f"score {hit['score']} ({hit['method']})"
        )
    for a in signals.get("schema_arithmetic", {}).get("arithmetic", []):
        if not a["correct"]:
            lines.append(f"  arithmetic     {a['message']}")
    ground = signals.get("grounding", {})
    lines.append(
        f"  grounding      score={ground.get('score')} status={ground.get('status')} "
        f"({ground.get('supported', 0)} supported / {ground.get('unsupported', 0)} unsupported)"
    )
    for c in ground.get("claims", []):
        for issue in c.get("issues", []):
            lines.append(f"                 {issue}")
    unc = signals.get("uncertainty", {})
    lines.append(f"  uncertainty    score={unc.get('score')} method={unc.get('method')}")
    lines.append(f"  ring 0 latency {signals.get('elapsed_us', 0)} microseconds")

    if detail.get("ring1_result"):
        r1 = detail["ring1_result"]
        lines += ["", f"RING 1 ({r1.get('verdict')}, {r1.get('latency_ms')} ms):"]
        lines += [f"  - {f}" for f in r1.get("findings", [])]

    if detail.get("overrides"):
        lines += ["", "HUMAN REVIEW:"]
        for o in detail["overrides"]:
            lines.append(
                f"  {o['created_at']} {o['reviewer_id']} decided '{o['decision']}'"
                + (f" -- {o['notes']}" if o.get("notes") else "")
            )
    if detail.get("threshold_adjustments"):
        lines += ["", "POLICY CHANGES TRIGGERED BY THIS REVIEW:"]
        for a in detail["threshold_adjustments"]:
            lines.append(f"  {a['field_changed']}: {a['old_value']} -> {a['new_value']}")
            lines.append(f"    reason: {a['reason']}")

    return {"response_id": response_id, "text": "\n".join(lines)}

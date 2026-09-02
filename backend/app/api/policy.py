from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.models import Policy, ThresholdAdjustment
from app.models.policy import DEFAULT_POLICIES
from app.rings.ring0.scorer import ACTION_EXPLANATIONS

router = APIRouter(prefix="/v1/policies", tags=["policy"])


class PolicyUpdate(BaseModel):
    label: str | None = None
    description: str | None = None
    jurisdiction: str | None = None
    risk_tolerance: str | None = None
    latency_budget_ms: int | None = None
    ring1_sample_rate: float | None = None
    ring1_spend_cap_pct: float | None = None
    pii_block_threshold: float | None = None
    grounding_flag_threshold: float | None = None
    uncertainty_flag_threshold: float | None = None
    cost_anomaly_z: float | None = None
    confidence_block_threshold: float | None = None
    flag_rate_slo: float | None = None
    blocked_entity_types: list[str] | None = None


class PolicyCreate(PolicyUpdate):
    use_case: str


@router.get("")
async def list_policies(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Policy).order_by(Policy.use_case))).scalars().all()
    return {"policies": [p.to_dict() for p in rows],
            "action_explanations": ACTION_EXPLANATIONS}


@router.get("/{use_case}")
async def get_policy(use_case: str, session: AsyncSession = Depends(get_session)):
    p = (await session.execute(
        select(Policy).where(Policy.use_case == use_case)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"no policy for use case '{use_case}'")
    return p.to_dict()


@router.post("")
async def create_policy(body: PolicyCreate, session: AsyncSession = Depends(get_session)):
    existing = (await session.execute(
        select(Policy).where(Policy.use_case == body.use_case)
    )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"policy '{body.use_case}' already exists")
    data = {k: v for k, v in body.model_dump().items() if v is not None}
    p = Policy(**data)
    session.add(p)
    await session.commit()
    return p.to_dict()


@router.put("/{use_case}")
async def update_policy(
    use_case: str, body: PolicyUpdate, session: AsyncSession = Depends(get_session)
):
    p = (await session.execute(
        select(Policy).where(Policy.use_case == use_case)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"no policy for use case '{use_case}'")

    changes = []
    for key, value in body.model_dump().items():
        if value is None:
            continue
        old = getattr(p, key)
        if old == value:
            continue
        setattr(p, key, value)
        # Manual edits are logged in the same table as automatic ones, so the
        # history chart tells the whole story of how a threshold got here.
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            session.add(ThresholdAdjustment(
                policy_id=p.id, use_case=p.use_case, field_changed=key,
                old_value=float(old), new_value=float(value),
                reason="changed manually from the policy console",
            ))
        changes.append(key)
    await session.commit()
    return {"policy": p.to_dict(), "changed": changes}


@router.get("/{use_case}/history")
async def policy_history(use_case: str, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(ThresholdAdjustment)
        .where(ThresholdAdjustment.use_case == use_case)
        .order_by(ThresholdAdjustment.created_at)
    )).scalars().all()
    return {"use_case": use_case, "adjustments": [r.to_dict() for r in rows]}


@router.post("/{use_case}/reset")
async def reset_policy(use_case: str, session: AsyncSession = Depends(get_session)):
    """Restore a policy to its shipped defaults. Handy between demo runs."""
    default = next((d for d in DEFAULT_POLICIES if d["use_case"] == use_case), None)
    if default is None:
        raise HTTPException(404, f"'{use_case}' has no shipped default")
    p = (await session.execute(
        select(Policy).where(Policy.use_case == use_case)
    )).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"no policy for use case '{use_case}'")
    for k, v in default.items():
        setattr(p, k, v)
    await session.commit()
    return p.to_dict()


async def seed_default_policies(session: AsyncSession) -> int:
    created = 0
    for spec in DEFAULT_POLICIES:
        existing = (await session.execute(
            select(Policy).where(Policy.use_case == spec["use_case"])
        )).scalar_one_or_none()
        if existing is None:
            session.add(Policy(**spec))
            created += 1
    if created:
        await session.commit()
    return created

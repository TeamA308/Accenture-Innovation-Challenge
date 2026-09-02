from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import bus, ring1_queue
from app.core.telemetry import cost_baseline, latency
from app.db.session import get_session
from app.models import LLMResponse, Override
from app.rings.ring1.budget import budget
from app.rings.ring1.worker import cache_stats
from app.rings.ring2.trust_metrics import per_policy_report, trust_report

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    """The numbers on the dashboard's top row."""
    rows = (await session.execute(
        select(
            func.count(LLMResponse.id),
            func.sum(LLMResponse.tokens_used),
            func.sum(LLMResponse.cost_usd),
            func.avg(LLMResponse.confidence),
        )
    )).one()
    total, tokens, spend, avg_conf = rows[0] or 0, rows[1] or 0, rows[2] or 0.0, rows[3] or 0.0

    by_action = dict((await session.execute(
        select(LLMResponse.final_action, func.count()).group_by(LLMResponse.final_action)
    )).all())
    by_ring1 = dict((await session.execute(
        select(LLMResponse.ring1_status, func.count()).group_by(LLMResponse.ring1_status)
    )).all())

    deep_checked = by_ring1.get("complete", 0) + by_ring1.get("pending", 0)
    flagged = sum(by_action.get(a, 0) for a in ("flag", "edit", "gate", "block"))

    ring1_spend = (await session.execute(
        select(func.sum(LLMResponse.ring1_cost_usd))
    )).scalar() or 0.0

    # FinOps: what the cost lane says is recoverable.
    recoverable = 0.0
    retries = 0
    over_modelled = 0
    signals_rows = (await session.execute(
        select(LLMResponse.ring0_signals).order_by(LLMResponse.created_at.desc()).limit(500)
    )).scalars().all()
    for s in signals_rows:
        c = (s or {}).get("cost", {})
        recoverable += float(c.get("potential_saving_usd", 0) or 0)
        retries += 1 if c.get("is_retry") else 0
        over_modelled += 1 if c.get("over_modelled") else 0

    return {
        "responses_checked": total,
        "tokens_used": int(tokens),
        "spend_usd": round(float(spend), 5),
        "avg_confidence": round(float(avg_conf), 3),
        "by_action": by_action,
        "by_ring1_status": by_ring1,
        "flag_rate": round(flagged / total, 4) if total else 0.0,
        "deep_check_rate": round(deep_checked / total, 4) if total else 0.0,
        "oversight": {
            "ring1_spend_usd": round(float(ring1_spend), 6),
            "ring1_spend_pct_of_inference": (
                round(float(ring1_spend) / float(spend) * 100, 3) if spend else 0.0
            ),
            "budget_by_use_case": budget.snapshot(),
            "cache": cache_stats(),
            "queue_depth": ring1_queue.qsize(),
            "dashboards_connected": bus.subscriber_count,
        },
        "finops": {
            "recoverable_usd_sampled": round(recoverable, 5),
            "retry_loops_detected": retries,
            "over_modelled_calls": over_modelled,
            "note": (
                "Recoverable spend is the difference between what a call cost and "
                "what the smallest adequate model would have cost, summed over the "
                "last 500 responses."
            ),
        },
        "latency": {
            "ring0": latency.percentiles("ring0"),
            "note": "Ring 0 runs on 100% of traffic. Figures are microseconds.",
        },
    }


@router.get("/latency")
async def latency_detail():
    return {"stages": latency.all_stages(),
            "cost_baselines": cost_baseline.snapshot()}


@router.get("/trust")
async def trust(use_case: str | None = None, session: AsyncSession = Depends(get_session)):
    return await trust_report(session, use_case)


@router.get("/trust/by-policy")
async def trust_by_policy(session: AsyncSession = Depends(get_session)):
    return {"policies": await per_policy_report(session)}


@router.get("/timeseries")
async def timeseries(hours: int = 24, session: AsyncSession = Depends(get_session)):
    """Hourly counts by action, for the dashboard chart."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (await session.execute(
        select(LLMResponse.created_at, LLMResponse.final_action, LLMResponse.cost_usd)
        .where(LLMResponse.created_at >= since)
        .order_by(LLMResponse.created_at)
    )).all()

    buckets: dict[str, dict] = {}
    for created, action, cost in rows:
        key = created.replace(minute=0, second=0, microsecond=0).isoformat()
        b = buckets.setdefault(key, {"t": key, "allow": 0, "edit": 0, "flag": 0,
                                     "gate": 0, "block": 0, "spend_usd": 0.0})
        if action in b:
            b[action] += 1
        b["spend_usd"] = round(b["spend_usd"] + float(cost or 0), 6)
    return {"buckets": list(buckets.values())}


@router.get("/finops")
async def finops(session: AsyncSession = Depends(get_session)):
    """Cost attributed by use case, intent and model."""
    rows = (await session.execute(
        select(LLMResponse.use_case, LLMResponse.model_name,
               func.count(), func.sum(LLMResponse.tokens_used),
               func.sum(LLMResponse.cost_usd))
        .group_by(LLMResponse.use_case, LLMResponse.model_name)
    )).all()
    return {
        "by_use_case_model": [
            {"use_case": uc, "model": m, "calls": n,
             "tokens": int(t or 0), "spend_usd": round(float(c or 0), 6)}
            for uc, m, n, t, c in rows
        ],
        "baselines": cost_baseline.snapshot(),
    }


@router.get("/reviewers")
async def reviewer_activity(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(Override.reviewer_id, Override.decision, func.count())
        .group_by(Override.reviewer_id, Override.decision)
    )).all()
    out: dict[str, dict] = {}
    for reviewer, decision, n in rows:
        out.setdefault(reviewer, {})[decision] = n
    return {"reviewers": out}

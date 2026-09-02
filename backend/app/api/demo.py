"""Demo support: curated prompts, source documents, and a load simulator.

The load simulator matters more than it looks. Our reference scale is tens of
thousands of interactions a week across several use cases, and a dashboard
showing four rows proves nothing about that. It replays synthetic traffic
through the real pipeline -- the same detectors, the same scorer, the same
policies -- so the latency percentiles and flag rates on screen are measured,
not mocked.
"""
from __future__ import annotations

import asyncio
import json
import random
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import REPO_ROOT
from app.core.orchestrator import GenerateRequest, generate_once
from app.db.session import SessionLocal, get_session
from app.models import Conversation, LLMResponse, Override, ThresholdAdjustment

router = APIRouter(prefix="/v1/demo", tags=["demo"])

SEED_DIR = REPO_ROOT / "seed"
DOCS_DIR = SEED_DIR / "simulated_docs"


def load_doc(name: str) -> str:
    path = DOCS_DIR / name
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_prompts() -> dict:
    path = SEED_DIR / "demo_prompts.json"
    if not path.exists():
        return {"prompts": []}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/prompts")
async def demo_prompts():
    data = load_prompts()
    for p in data.get("prompts", []):
        p["context_docs"] = [load_doc(d) for d in p.get("docs", [])]
    return data


@router.get("/documents")
async def documents():
    return {
        "documents": [
            {"name": f.name, "text": f.read_text(encoding="utf-8")}
            for f in sorted(DOCS_DIR.glob("*.md"))
        ]
    }


class SimulateBody(BaseModel):
    count: int = 60
    concurrency: int = 8
    # When set, the generated rows are dated back across this many hours so the
    # dashboard's time series looks like history rather than one spike, and the
    # rolling oversight budget is released for live traffic.
    spread_hours: float | None = None


# Weights approximate a real fleet: the overwhelming majority of traffic is
# routine and well grounded, a minority sits in the grey zone, and a small tail
# is genuinely bad. A demo dataset where half of everything is on fire would
# make the flag rate meaningless.
_TRAFFIC = [
    ("What does the warranty cover on a laptop bought eight months ago?",
     "customer_facing", ["northwind_warranty_terms.md"], True, "draft", 0.31),
    ("When are your business hours and how do I contact support?",
     "customer_facing", ["northwind_warranty_terms.md"], True, "draft", 0.23),
    ("Summarise what changed in the renewal terms for the account team.",
     "internal_copilot", [], True, "draft", 0.17),
    ("What does our warranty cover and when is support available? For the onboarding deck.",
     "internal_copilot", ["northwind_warranty_terms.md"], True, "draft", 0.11),
    ("What is the service credit if Helios misses the Severity 1 restoration target?",
     "internal_copilot", ["helios_support_sla.md"], True, "draft", 0.055),
    ("A customer wants a refund on a clearance item bought 40 days ago. Do they qualify?",
     "customer_facing", ["northwind_refund_policy.md"], True, "draft", 0.05),
    ("Give me the pricing breakdown for the Helios renewal quote with GST.",
     "internal_copilot", [], True, "draft", 0.035),
    ("Confirm the warranty duration and exclusions for the compliance file.",
     "decision_support_regulated", ["northwind_warranty_terms.md"], True, "draft", 0.08),
    ("Should we approve Priya Sharma's requested credit limit of Rs 1200000?",
     "decision_support_regulated", [], False, "api_commit", 0.03),
    ("Prepare the payment to settle Helios invoice HC-2291.",
     "decision_support_regulated", [], False, "payment", 0.02),
    ("Pull up the full account details for the escalation on ticket 44120.",
     "customer_facing", [], True, "draft", 0.015),
]


@router.post("/simulate")
async def simulate(body: SimulateBody):
    """Replay synthetic traffic through the real pipeline."""
    count = max(1, min(body.count, 2000))
    weights = [t[5] for t in _TRAFFIC]
    sem = asyncio.Semaphore(max(1, min(body.concurrency, 32)))
    results: list[str] = []

    errors: list[str] = []

    async def one(i: int) -> None:
        prompt, use_case, docs, reversible, action, _ = random.choices(_TRAFFIC, weights)[0]
        # Most simulated requests are their own session; roughly a fifth are a
        # second turn of an earlier one, so multi-turn risk carry-forward gets
        # exercised too.
        session_id = f"sim-{i - 1}" if i % 5 == 0 and i > 0 else f"sim-{i}"
        async with sem:
            try:
                async with SessionLocal() as session:
                    out = await generate_once(session, GenerateRequest(
                        prompt=prompt,
                        use_case=use_case,
                        context_docs=[load_doc(d) for d in docs],
                        is_reversible=reversible,
                        downstream_action=action,
                        session_id=session_id,
                        stream_delay=0.0,
                    ))
                    results.append(out.get("action", "?"))
            except Exception as exc:  # one bad request must not abort the replay
                errors.append(f"{type(exc).__name__}: {exc}")

    await asyncio.gather(*(one(i) for i in range(count)))

    if body.spread_hours:
        await _spread_over_time(body.spread_hours)

    breakdown: dict[str, int] = {}
    for a in results:
        breakdown[a] = breakdown.get(a, 0) + 1
    return {
        "generated": len(results),
        "by_action": breakdown,
        "failed": len(errors),
        "errors": errors[:5],
    }


async def _spread_over_time(hours: float) -> None:
    """Date the freshly generated rows back across a window.

    Everything in them is genuinely measured -- the verdicts, the signals, the
    latencies. Only the clock is adjusted, so a demo does not open on a chart
    with a single bar in it, and so an hour of synthetic traffic does not hold
    the oversight budget against the first live request.
    """
    from datetime import timedelta

    from app.models.response import utcnow
    from app.rings.ring1.budget import budget

    now = utcnow()
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(LLMResponse).order_by(LLMResponse.created_at)
        )).scalars().all()
        if not rows:
            return
        span = hours * 3600.0
        for i, row in enumerate(rows):
            # Weight recent hours more heavily, the way real traffic behaves.
            frac = (i / max(1, len(rows) - 1)) ** 0.7
            row.created_at = now - timedelta(seconds=span * (1 - frac))
        await session.commit()
    budget.reset()


@router.post("/reset")
async def reset(session: AsyncSession = Depends(get_session)):
    """Wipe traffic and review history. Policies are left alone."""
    for model in (ThresholdAdjustment, Override, LLMResponse, Conversation):
        await session.execute(delete(model))
    await session.commit()
    from app.rings.ring1.worker import clear_cache

    clear_cache()
    return {"status": "cleared", "note": "policies and their thresholds were not reset"}


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)):
    n = len((await session.execute(select(LLMResponse.id))).scalars().all())
    return {"responses": n, "seed_dir": str(SEED_DIR), "documents_found": len(
        list(DOCS_DIR.glob("*.md")))}

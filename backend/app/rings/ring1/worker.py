"""Ring 1 - the async worker.

Ring 0 never waits for this. A job is enqueued, the user already has their
answer, and the worker resolves the verdict a beat later and pushes the update
to every open dashboard over a WebSocket.

The worker runs three checks and combines them into one verdict:

    judge         a cheaper model re-derives the answer independently
    faithfulness  every atomic claim is matched against the source documents
    counterfactual  the prompt is re-run with one protected attribute swapped

Results are cached on a hash of (prompt, response, policy thresholds). Rerun
the same prompt during rehearsal or a judge's question and the deep check
returns instantly -- a real cache hit, not a shortcut.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time

from sqlalchemy import select

from app.core.bus import bus, ring1_queue
from app.db.session import SessionLocal
from app.models import LLMResponse, Policy
from app.rings.ring1.budget import budget
from app.rings.ring1.counterfactual import counterfactual_probe, detect_protected_attribute
from app.rings.ring1.retrieval_check import faithfulness_check
from app.rings.ring1.verifier_judge import judge_verify

log = logging.getLogger("controlplane.ring1")

_CACHE: dict[str, dict] = {}
CACHE_LIMIT = 500

# Ring 1 escalates a verdict when faithfulness falls below this.
FAITHFULNESS_ESCALATE = 0.5


def cache_key(prompt: str, response_text: str, policy: Policy | None) -> str:
    parts = [
        prompt,
        response_text,
        str(getattr(policy, "grounding_flag_threshold", "")),
        str(getattr(policy, "uncertainty_flag_threshold", "")),
    ]
    return hashlib.sha256("||".join(parts).encode("utf-8")).hexdigest()


async def run_ring1_checks(
    *,
    prompt: str,
    response_text: str,
    context_docs: list[str],
    provider: str,
    model: str,
    judge_provider: str,
    judge_model: str,
    similarity_threshold: float = 0.75,
    run_counterfactual: bool = True,
) -> dict:
    """Run the three deep checks concurrently and fold them into one verdict."""
    t0 = time.perf_counter()

    async def _judge():
        return await judge_verify(prompt, response_text, context_docs,
                                  provider=judge_provider, model=judge_model)

    async def _faith():
        # CPU-bound and fast; run it off the event loop so it cannot stall the
        # two model calls happening beside it.
        return await asyncio.to_thread(faithfulness_check, response_text, context_docs)

    async def _counter():
        if not run_counterfactual or detect_protected_attribute(prompt) is None:
            return {"ran": False, "bias_flag": False,
                    "reason": "no protected attribute found in the prompt to swap"}
        return await counterfactual_probe(
            prompt, provider, model, response_text,
            similarity_threshold=similarity_threshold,
        )

    judge, faith, counter = await asyncio.gather(
        _judge(), _faith(), _counter(), return_exceptions=True
    )

    def _unwrap(name, value, fallback):
        if isinstance(value, BaseException):
            log.exception("ring1 %s failed", name, exc_info=value)
            return {**fallback, "error": f"{type(value).__name__}: {value}"}
        return value

    judge = _unwrap("judge", judge, {"ran": False, "agrees": None, "confidence": 0.0})
    faith = _unwrap("faithfulness", faith, {"ran": False, "faithfulness_score": None})
    counter = _unwrap("counterfactual", counter, {"ran": False, "bias_flag": False})

    findings: list[str] = []
    escalate = False

    if judge.get("ran") and judge.get("agrees") is False:
        escalate = True
        findings.append(f"verifier disagrees: {judge.get('judge_reasoning', '')}")
    elif not judge.get("ran"):
        findings.append("verifier unavailable; this response stays flagged for a human")

    fs = faith.get("faithfulness_score")
    if fs is not None and fs < FAITHFULNESS_ESCALATE:
        escalate = True
        findings.append(
            f"faithfulness {fs:.2f}: {faith.get('n_unsupported', 0)} of "
            f"{faith.get('n_checkable', 0)} atomic claims are unsupported by the sources"
        )
    for uc in faith.get("unsupported_claims", [])[:4]:
        for issue in uc.get("issues", [])[:2]:
            findings.append(f"claim \"{uc['claim'][:80]}\" -- {issue}")

    if counter.get("bias_flag"):
        escalate = True
        findings.append(counter.get("summary", "counterfactual twin diverged"))

    cost = round(float(judge.get("cost_usd", 0) or 0) + float(counter.get("twin_cost_usd", 0) or 0), 6)

    if escalate:
        verdict = "escalate"
    elif judge.get("ran") and fs is not None:
        verdict = "confirm"
    else:
        verdict = "inconclusive"

    return {
        "verdict": verdict,
        "escalate": escalate,
        "findings": findings,
        "judge": judge,
        "faithfulness": faith,
        "counterfactual": counter,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "cost_usd": cost,
        "cached": False,
    }


async def process_job(job: dict) -> None:
    response_id = job.get("response_id")
    if not response_id:
        return

    async with SessionLocal() as session:
        row = await session.get(LLMResponse, response_id)
        if row is None:
            return
        policy = (
            await session.execute(select(Policy).where(Policy.use_case == row.use_case))
        ).scalar_one_or_none()

        key = cache_key(row.prompt, row.response_text, policy)
        cached = _CACHE.get(key)
        if cached is not None:
            result = {**cached, "cached": True, "latency_ms": 1}
        else:
            result = await run_ring1_checks(
                prompt=row.prompt,
                response_text=row.response_text,
                context_docs=row.context_docs or [],
                provider=row.model_provider,
                model=row.model_name,
                judge_provider=job.get("judge_provider", "mock"),
                judge_model=job.get("judge_model", "controlplane-sim-judge"),
            )
            if len(_CACHE) < CACHE_LIMIT:
                _CACHE[key] = result

        result["audit_sample"] = bool(job.get("audit_sample"))
        if job.get("audit_sample"):
            result["findings"] = [
                "random audit of a response Ring 0 allowed (false-negative sampling)"
            ] + result["findings"]

        row.ring1_result = result
        row.ring1_status = "complete"
        row.ring1_latency_ms = result["latency_ms"]
        row.ring1_cost_usd = result["cost_usd"]

        previous = row.final_action
        if result["escalate"]:
            if row.action == "allow":
                row.final_action = "gate" if not row.is_reversible else "flag"
            elif row.action in ("edit", "flag"):
                row.final_action = "gate" if not row.is_reversible else "flag"
            else:
                row.final_action = row.action
        else:
            # A confirmed grey-zone answer is released. Blocks are never
            # downgraded by Ring 1 -- a deterministic violation stays blocked.
            if row.action in ("flag", "edit", "gate") and result["verdict"] == "confirm":
                row.final_action = "allow" if row.action != "gate" else "allow"

        if row.gate_state == "gated":
            if result["escalate"]:
                row.gate_state = "withheld"
            elif result["verdict"] == "confirm":
                row.gate_state = "released"
            else:
                # Inconclusive is not the same as clear. An irreversible action
                # whose deep check could not reach a conclusion stays held for a
                # human -- releasing it would make "we could not check this" and
                # "we checked this and it is fine" mean the same thing.
                row.gate_state = "gated"
                row.final_action = "gate"

        await session.commit()
        payload = row.to_summary()

    budget.record_ring1(job.get("use_case", ""), result["cost_usd"], response_id)
    await bus.publish({
        "type": "ring1_complete",
        "response_id": response_id,
        "previous_action": previous,
        "response": payload,
        "ring1": {
            "verdict": result["verdict"],
            "escalate": result["escalate"],
            "findings": result["findings"][:6],
            "cached": result.get("cached", False),
            "latency_ms": result["latency_ms"],
            "bias_flag": result.get("counterfactual", {}).get("bias_flag", False),
        },
    })


async def worker_loop(stop: asyncio.Event) -> None:
    log.info("ring 1 worker started")
    while not stop.is_set():
        try:
            job = await asyncio.wait_for(ring1_queue.dequeue(), timeout=1.0)
        except (asyncio.TimeoutError, TimeoutError):
            continue
        except asyncio.CancelledError:
            break
        except Exception:  # pragma: no cover - defensive
            log.exception("ring1 dequeue failed")
            await asyncio.sleep(0.5)
            continue
        if not job:
            continue
        try:
            await process_job(job)
        except Exception:  # pragma: no cover - a bad job must not kill the worker
            log.exception("ring1 job failed for %s", job.get("response_id"))
            with contextlib.suppress(Exception):
                async with SessionLocal() as session:
                    row = await session.get(LLMResponse, job.get("response_id"))
                    if row is not None:
                        row.ring1_status = "failed"
                        row.ring1_result = {"verdict": "unavailable",
                                            "findings": ["Ring 1 check failed; left flagged"]}
                        await session.commit()
    log.info("ring 1 worker stopped")


def cache_stats() -> dict:
    return {"entries": len(_CACHE), "limit": CACHE_LIMIT}


def clear_cache() -> None:
    _CACHE.clear()


def _json_safe(obj) -> str:
    return json.dumps(obj, default=str)

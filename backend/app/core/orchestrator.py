"""The request path.

One function describes the whole product, so it is worth reading in order:

  1. Look up the policy for this use case. Every threshold below comes from it.
  2. Stream the model's tokens straight to the user. Nothing blocks here.
  3. While those tokens stream, re-scan the partial text for deterministic
     violations. If a credential or an identity number appears, cut the stream
     mid-sentence -- a leak caught after the user has read it is not a control.
  4. When the text completes, run every Ring 0 check and score them against
     the policy. Microseconds, on 100% of traffic.
  5. Redact if we must, persist the row, update the session's carried risk.
  6. Decide whether this response earns a Ring 1 deep check, subject to the
     budget. Enqueue it and return -- the user is already reading the answer.

The user waits for step 2. Everything after it happens beside them.
"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.bus import bus, ring1_queue
from app.core.config import settings
from app.core.llm_gateway import LLMGateway
from app.models import Conversation, LLMResponse, Policy
from app.rings.ring0.pii import redact
from app.rings.ring0.pipeline import run_ring0, scan_stream_partial
from app.rings.ring0.secrets import redact_secrets
from app.rings.ring1.budget import budget

log = logging.getLogger("controlplane.orchestrator")

# How often the mid-stream deterministic scan runs, in chunks.
STREAM_SCAN_EVERY = 12
# Share of allowed traffic randomly deep-checked to measure misses.
AUDIT_SAMPLE_RATE = 0.03

BLOCK_NOTICE = (
    "\n\n--- RESPONSE WITHHELD BY CONTROLPLANE ---\n"
    "This answer was stopped mid-generation because it contained {what}. "
    "The redacted text is retained in the audit record and is available to an "
    "authorised reviewer."
)


@dataclass
class GenerateRequest:
    prompt: str
    use_case: str = "internal_copilot"
    model_provider: str | None = None
    model_name: str | None = None
    context_docs: list[str] = field(default_factory=list)
    is_reversible: bool = True
    downstream_action: str = "draft"
    session_id: str | None = None
    expected_schema: dict | None = None
    stream_delay: float | None = None


async def get_policy(session: AsyncSession, use_case: str) -> Policy | None:
    return (
        await session.execute(select(Policy).where(Policy.use_case == use_case))
    ).scalar_one_or_none()


async def _load_conversation(session: AsyncSession, session_id: str, use_case: str) -> Conversation:
    convo = await session.get(Conversation, session_id)
    if convo is not None:
        return convo
    convo = Conversation(id=session_id, use_case=use_case, turns=0,
                         accumulated_risk=0.0, history=[])
    session.add(convo)
    try:
        await session.flush()
    except IntegrityError:
        # Two concurrent turns of the same session raced to create it. Whoever
        # lost simply picks up the row the winner inserted.
        await session.rollback()
        existing = await session.get(Conversation, session_id)
        if existing is None:
            raise
        return existing
    return convo


def _update_conversation(convo: Conversation, prompt: str, verdict: dict) -> None:
    """Carry risk forward across turns.

    A single shaky answer is a nuisance. The same answer used as the premise
    for three more turns, one of which triggers a payment, is an incident. Risk
    decays but does not reset, so a conversation that has already gone wrong is
    held to a higher standard than a fresh one.
    """
    turn_risk = 1.0 - verdict["confidence"]
    convo.accumulated_risk = round(min(1.0, convo.accumulated_risk * 0.7 + turn_risk * 0.6), 4)
    convo.turns += 1
    if verdict["action"] != "allow":
        convo.flagged_turns += 1
    history = list(convo.history or [])
    history.append({"prompt": prompt[:280], "action": verdict["action"],
                    "confidence": verdict["confidence"]})
    convo.history = history[-12:]


async def _session_prompts(session: AsyncSession, session_id: str) -> list[str]:
    rows = (
        await session.execute(
            select(LLMResponse.prompt)
            .where(LLMResponse.session_id == session_id)
            .order_by(desc(LLMResponse.created_at))
            .limit(5)
        )
    ).scalars().all()
    return list(rows)


async def stream_generate(
    session: AsyncSession, req: GenerateRequest
) -> AsyncIterator[dict]:
    """Yield event dicts: {'type': 'token'|'blocked'|'verdict'|'error', ...}."""
    t_start = time.perf_counter()
    provider = req.model_provider or settings.default_provider
    model = req.model_name or settings.default_model
    session_id = req.session_id or f"sess-{int(time.time() * 1000)}"

    policy = await get_policy(session, req.use_case)
    convo = await _load_conversation(session, session_id, req.use_case)
    prior_risk = convo.accumulated_risk
    history = await _session_prompts(session, session_id)

    gateway = LLMGateway()
    if provider == "mock" and req.stream_delay is not None:
        from app.core.providers.mock import MockProvider

        gateway._build = lambda _p: MockProvider(stream_delay=req.stream_delay)  # noqa: SLF001

    yield {"type": "start", "session_id": session_id, "use_case": req.use_case,
           "provider": provider, "model": model,
           "policy": policy.to_dict() if policy else None}

    collected: list[str] = []
    stream_violation: dict | None = None
    chunk_count = 0

    async for chunk in gateway.generate(req.prompt, provider, model):
        if not chunk:
            continue
        collected.append(chunk)
        chunk_count += 1
        yield {"type": "token", "text": chunk}

        if chunk_count % STREAM_SCAN_EVERY == 0:
            violation = scan_stream_partial("".join(collected), policy)
            if violation:
                stream_violation = violation
                yield {
                    "type": "stream_halted",
                    "entity_types": violation["entity_types"],
                    "message": (
                        "Ring 0 stopped this stream mid-generation: "
                        + ", ".join(violation["entity_types"])
                    ),
                }
                break

    raw_text = "".join(collected)
    gen = gateway.result()

    signals, verdict, ring0_us = run_ring0(
        prompt=req.prompt,
        response_text=raw_text,
        context_docs=req.context_docs,
        policy=policy,
        use_case=req.use_case,
        model_name=model,
        tokens_in=gen.tokens_in,
        tokens_out=gen.tokens_out,
        cost_usd=gen.cost_usd,
        token_logprobs=gen.token_logprobs,
        expected_schema=req.expected_schema,
        is_reversible=req.is_reversible,
        downstream_action=req.downstream_action,
        session_history=history,
        conversation_risk=prior_risk,
    )
    if stream_violation:
        verdict["reasons"].insert(
            0, "stream halted mid-generation by the inline deterministic scan"
        )

    display_text = raw_text
    redacted_text = None
    if verdict["needs_redaction"]:
        redacted_text = redact_secrets(
            redact(raw_text, signals["pii"], verdict["redact_entity_types"]),
            signals["secrets"],
        )
        display_text = redacted_text + BLOCK_NOTICE.format(
            what=", ".join(verdict["redact_entity_types"])
        )

    row = LLMResponse(
        session_id=session_id,
        turn_index=convo.turns,
        prompt=req.prompt,
        use_case=req.use_case,
        context_docs=req.context_docs,
        is_reversible=req.is_reversible,
        downstream_action=req.downstream_action,
        model_provider=provider,
        model_name=model,
        response_text=raw_text,
        redacted_text=redacted_text,
        tokens_in=gen.tokens_in,
        tokens_out=gen.tokens_out,
        tokens_used=gen.tokens_in + gen.tokens_out,
        cost_usd=gen.cost_usd,
        latency_ms=int((time.perf_counter() - t_start) * 1000),
        ring0_latency_us=ring0_us,
        ring0_signals=signals,
        confidence=verdict["confidence"],
        action=verdict["action"],
        action_reasons=verdict["reasons"],
        final_action=verdict["action"],
        gate_state="gated" if verdict["gate_required"] else "open",
    )
    session.add(row)
    _update_conversation(convo, req.prompt, verdict)
    await session.commit()

    budget.record_request(req.use_case, gen.cost_usd, row.id)
    ring1_status, ring1_reason = await _maybe_enqueue_ring1(row, policy, verdict)
    row.ring1_status = ring1_status
    row.ring1_reason = ring1_reason
    await session.commit()

    summary = row.to_summary()
    await bus.publish({"type": "response_created", "response": summary,
                       "conversation": convo.to_dict()})

    yield {
        "type": "verdict",
        "response_id": row.id,
        "action": verdict["action"],
        "confidence": verdict["confidence"],
        "reasons": verdict["reasons"],
        "display_text": display_text,
        "redacted": bool(redacted_text),
        "ring0_latency_us": ring0_us,
        "ring1_status": ring1_status,
        "ring1_reason": ring1_reason,
        "gate_state": row.gate_state,
        "signals": signals,
        "response": summary,
        "conversation": convo.to_dict(),
        "scenario": gateway.scenario_key,
    }


async def _maybe_enqueue_ring1(row: LLMResponse, policy, verdict: dict) -> tuple[str, str]:
    """Admission control for the deep check."""
    import random

    if verdict["deterministic_violation"]:
        # A blocked response is already resolved. Paying a second model to
        # confirm a checksum would be pure waste.
        return "skipped", "deterministic violation; already resolved without a second model"

    audit = False
    if not verdict["ring1_recommended"]:
        if verdict["action"] == "allow" and random.random() < AUDIT_SAMPLE_RATE:
            audit = True  # measure our misses, not just our hits
        else:
            return "skipped", "Ring 0 verdict was conclusive; no deep check needed"

    admitted, why = budget.admit(
        row.use_case, policy, verdict["ring1_priority"], is_audit=audit, response_id=row.id
    )
    if not admitted:
        return "deferred", why

    await ring1_queue.enqueue({
        "response_id": row.id,
        "use_case": row.use_case,
        "priority": verdict["ring1_priority"],
        "audit_sample": audit,
        "judge_provider": settings.judge_provider,
        "judge_model": settings.judge_model,
    })
    return "pending", ("random audit of an allowed response" if audit
                       else f"grey zone: {verdict['driving_signal']}")


async def generate_once(session: AsyncSession, req: GenerateRequest) -> dict:
    """Non-streaming convenience path, used by tests and the load simulator."""
    final: dict = {}
    async for event in stream_generate(session, req):
        if event["type"] == "verdict":
            final = event
    return final

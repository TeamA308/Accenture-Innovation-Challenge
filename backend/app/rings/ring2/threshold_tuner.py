"""Ring 2 - learning from human verdicts.

Over-flagging causes alert fatigue and people start ignoring the warnings.
Under-flagging causes liability. Nobody solves that trade-off; you tune it, and
you keep tuning it, because the traffic changes.

So every human override is a labelled example, and this module turns a run of
them into a threshold change. The rules are deliberately simple and legible,
because a governance system that adjusts itself in ways nobody can explain is
not an improvement on one that never adjusts at all.

    A reviewer accepts something we flagged   -> we were wrong to flag it (false positive)
    A reviewer rejects something we flagged   -> we were right (true positive)
    A reviewer rejects something we allowed   -> we missed it (false negative)

When the false-positive rate for one signal, in one policy, over the last N
overrides, crosses the trigger rate, we loosen that signal's threshold by one
small step. A single false negative tightens it immediately -- the costs are
not symmetric, and the tuner should not pretend they are.

What is NOT tunable
-------------------
Deterministic checks. A validated card number is not a matter of opinion, and
no volume of reviewer disagreement will make this module relax a PII block
below its floor or touch the arithmetic checker. Those are the checks we can
prove, and negotiating them away would hollow out the whole product.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LLMResponse, Override, Policy, ThresholdAdjustment

log = logging.getLogger("controlplane.ring2.tuner")

# How many recent overrides for a policy form the evidence window.
WINDOW = 5
# False-positive rate that triggers a loosening step.
FP_TRIGGER = 0.30
STEP = 0.05
# Minimum gap between two flag-rate service-level corrections on one policy.
SLO_COOLDOWN = timedelta(hours=1)

# signal -> (policy field, direction that REDUCES flags, floor, ceiling)
# direction -1 means "lower the number to flag less"; +1 means "raise it".
TUNABLE: dict[str, tuple[str, int, float, float]] = {
    "grounding": ("grounding_flag_threshold", -1, 0.20, 0.95),
    "uncertainty": ("uncertainty_flag_threshold", +1, 0.20, 0.95),
    "conversation": ("confidence_block_threshold", -1, 0.05, 0.60),
}

# Signals whose thresholds this module will not touch, and why.
NOT_TUNABLE = {
    "pii": "personal-data detection is deterministic and validated by checksum; "
           "its threshold is not adjusted by reviewer sentiment",
    "secret": "credential detection is deterministic; not adjusted automatically",
    "arithmetic": "arithmetic is verified by recomputation, so there is no threshold to tune",
    "degraded_detector": "the flag came from a detector outage, not from a threshold",
    "none": "no probabilistic signal drove this verdict",
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


async def retune_thresholds(
    session: AsyncSession,
    policy: Policy,
    override: Override,
    response: LLMResponse,
) -> list[ThresholdAdjustment]:
    """Apply the learning rule. Returns any adjustments made (usually zero or one)."""
    signal = override.driving_signal or "none"
    adjustments: list[ThresholdAdjustment] = []

    if signal not in TUNABLE:
        log.info("override on non-tunable signal %s: %s", signal,
                 NOT_TUNABLE.get(signal, "unknown signal"))
        return adjustments

    field, direction, floor, ceiling = TUNABLE[signal]

    # --- immediate tighten on a miss ---------------------------------------
    # A false negative is a response we let through that a human then rejected.
    if override.machine_action == "allow" and override.decision == "reject":
        old = getattr(policy, field)
        new = _clamp(old - direction * STEP, floor, ceiling)
        if new != old:
            setattr(policy, field, new)
            adjustments.append(_log_adjustment(
                policy, field, old, new, override.id,
                reason=(
                    f"A reviewer rejected a response that Ring 0 allowed -- a missed "
                    f"detection on the {signal} signal. Tightening immediately: "
                    f"the cost of a miss is not symmetric with the cost of a false alarm."
                ),
            ))
            session.add(adjustments[-1])
        return adjustments

    # --- loosen only on a sustained run of false positives ------------------
    recent = (
        await session.execute(
            select(Override)
            .where(Override.use_case == policy.use_case)
            .where(Override.driving_signal == signal)
            .order_by(desc(Override.created_at))
            .limit(WINDOW)
        )
    ).scalars().all()

    if len(recent) < WINDOW:
        log.info("tuner: %d/%d overrides on %s for %s -- not enough evidence yet",
                 len(recent), WINDOW, signal, policy.use_case)
        return adjustments

    false_positives = sum(
        1 for o in recent
        if o.decision == "accept" and o.machine_action in ("flag", "edit", "gate", "block")
    )
    fp_rate = false_positives / len(recent)
    if fp_rate < FP_TRIGGER:
        return adjustments

    old = getattr(policy, field)
    new = _clamp(old + direction * STEP, floor, ceiling)
    if new == old:
        log.info("tuner: %s already at its bound (%.2f)", field, old)
        return adjustments

    setattr(policy, field, new)
    adj = _log_adjustment(
        policy, field, old, new, override.id,
        reason=(
            f"{false_positives} of the last {len(recent)} reviewer decisions on the "
            f"{signal} signal for '{policy.use_case}' said we were wrong to flag "
            f"({fp_rate:.0%} false positives, trigger is {FP_TRIGGER:.0%}). "
            f"Loosening {field} by {STEP} to cut alert fatigue. Deterministic checks "
            f"are unaffected."
        ),
    )
    session.add(adj)
    adjustments.append(adj)
    return adjustments


def _log_adjustment(policy, field, old, new, override_id, reason) -> ThresholdAdjustment:
    log.info("tuner: %s.%s %.3f -> %.3f (%s)", policy.use_case, field, old, new, reason)
    return ThresholdAdjustment(
        policy_id=policy.id,
        use_case=policy.use_case,
        field_changed=field,
        old_value=float(old),
        new_value=float(new),
        reason=reason,
        triggered_by_override_id=override_id,
    )


async def flag_rate_check(session: AsyncSession, policy: Policy) -> ThresholdAdjustment | None:
    """Steer towards the policy's flag-rate service level.

    Thresholds that only ever move on individual overrides can drift. This
    keeps the overall share of flagged traffic near the number the policy owner
    signed up for, which is the promise that actually matters to the team
    living with the alerts.
    """
    # One service-level correction per hour per policy. Without a cooldown a
    # sustained over-flagging period would ratchet the threshold down on every
    # single override, which is a control loop that oscillates rather than one
    # that settles.
    recent_slo = (
        await session.execute(
            select(ThresholdAdjustment)
            .where(ThresholdAdjustment.use_case == policy.use_case)
            .where(ThresholdAdjustment.triggered_by_override_id.is_(None))
            .order_by(desc(ThresholdAdjustment.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    if recent_slo is not None:
        last = recent_slo.created_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last) < SLO_COOLDOWN:
            return None

    # The per-signal rule is the fast loop and gets first refusal, because it
    # acts on evidence about a specific signal. This one is the slow loop: it
    # only speaks once reviewers have said enough for their workload to be a
    # measured fact rather than an impression.
    n_overrides = len((
        await session.execute(
            select(Override.id).where(Override.use_case == policy.use_case)
        )
    ).scalars().all())
    if n_overrides < WINDOW:
        return None

    rows = (
        await session.execute(
            select(LLMResponse.final_action)
            .where(LLMResponse.use_case == policy.use_case)
            .order_by(desc(LLMResponse.created_at))
            .limit(100)
        )
    ).scalars().all()
    if len(rows) < 40:
        return None

    flagged = sum(1 for a in rows if a in ("flag", "edit", "gate"))
    rate = flagged / len(rows)
    slo = policy.flag_rate_slo
    if rate <= slo * 1.5:
        return None

    old = policy.grounding_flag_threshold
    new = _clamp(old - STEP, 0.20, 0.95)
    if new == old:
        return None
    policy.grounding_flag_threshold = new
    adj = _log_adjustment(
        policy, "grounding_flag_threshold", old, new, None,
        reason=(
            f"{flagged} of the last {len(rows)} responses for '{policy.use_case}' were "
            f"flagged ({rate:.0%}), well above this policy's {slo:.0%} flag-rate "
            f"service level. Loosening to protect reviewers from alert fatigue."
        ),
    )
    session.add(adj)
    return adj

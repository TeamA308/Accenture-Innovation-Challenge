"""Ring 2 - reporting trustworthiness to a sceptical stakeholder.

"How do you know your checker is any good?" is the question that ends most
demos of this kind of tool, and it deserves a real answer rather than a
confidence bar. This module computes one from data the system already has.

The ground truth is the reviewer. Every override is a label:

                          reviewer says harmful   reviewer says fine
    we flagged / blocked      true positive        false positive
    we allowed                false negative       true negative

False negatives are the hard part, because nobody reviews the responses you
let through. We get them from the audit sample: a slice of the Ring 1 budget is
spent deep-checking traffic that Ring 0 cleared, precisely so that misses can
be counted rather than assumed to be zero.

Everything here is reported with its sample size, and we say plainly when the
sample is too small to conclude anything. A governance tool that reports 100%
precision on four data points is not being helpful.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LLMResponse, Override, Policy, ThresholdAdjustment

FLAGGING_ACTIONS = ("flag", "edit", "gate", "block")
MIN_SAMPLE_FOR_CONFIDENCE = 10


def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes even for timezone-aware columns.

    Anything read from the database is UTC by construction, so label it as such
    rather than letting a naive/aware comparison blow up mid-report.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def trust_report(session: AsyncSession, use_case: str | None = None) -> dict:
    q = select(Override)
    if use_case:
        q = q.where(Override.use_case == use_case)
    overrides = (await session.execute(q)).scalars().all()

    tp = sum(1 for o in overrides
             if o.machine_action in FLAGGING_ACTIONS and o.decision in ("reject", "edit"))
    fp = sum(1 for o in overrides
             if o.machine_action in FLAGGING_ACTIONS and o.decision == "accept")
    fn = sum(1 for o in overrides if o.machine_action == "allow" and o.decision != "accept")
    tn = sum(1 for o in overrides if o.machine_action == "allow" and o.decision == "accept")

    # Misses found by the audit sample, which is where most false negatives
    # actually come from.
    rq = select(LLMResponse).where(LLMResponse.ring1_status == "complete")
    if use_case:
        rq = rq.where(LLMResponse.use_case == use_case)
    checked = (await session.execute(rq)).scalars().all()
    audit_rows = [r for r in checked if (r.ring1_result or {}).get("audit_sample")]
    audit_misses = sum(1 for r in audit_rows if (r.ring1_result or {}).get("escalate"))

    total_labelled = tp + fp + fn + tn
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn + audit_misses) if (tp + fn + audit_misses) else None
    fpr = fp / (fp + tn) if (fp + tn) else None
    f1 = (2 * precision * recall / (precision + recall)) if precision and recall else None

    vq = select(LLMResponse.final_action, func.count()).group_by(LLMResponse.final_action)
    if use_case:
        vq = vq.where(LLMResponse.use_case == use_case)
    action_counts = {a: c for a, c in (await session.execute(vq)).all()}
    total_responses = sum(action_counts.values())
    flagged = sum(action_counts.get(a, 0) for a in FLAGGING_ACTIONS)

    # Mean time from response to human verdict -- the "weeks to seconds" claim,
    # measured.
    latencies: list[float] = []
    by_id = {r.id: r for r in checked}
    for o in overrides:
        row = by_id.get(o.response_id)
        if row is None:
            row = await session.get(LLMResponse, o.response_id)
        if row is not None and row.created_at and o.created_at:
            latencies.append((_utc(o.created_at) - _utc(row.created_at)).total_seconds())
    mttr = sum(latencies) / len(latencies) if latencies else None

    since = datetime.now(timezone.utc) - timedelta(days=30)
    aq = select(func.count()).select_from(ThresholdAdjustment).where(
        ThresholdAdjustment.created_at >= since
    )
    if use_case:
        aq = aq.where(ThresholdAdjustment.use_case == use_case)
    n_adjustments = (await session.execute(aq)).scalar() or 0

    enough = total_labelled >= MIN_SAMPLE_FOR_CONFIDENCE
    return {
        "use_case": use_case or "all",
        "confusion_matrix": {
            "true_positive": tp, "false_positive": fp,
            "false_negative": fn + audit_misses, "true_negative": tn,
        },
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "false_positive_rate": round(fpr, 3) if fpr is not None else None,
        "f1": round(f1, 3) if f1 is not None else None,
        "labelled_sample": total_labelled,
        "sample_is_sufficient": enough,
        "caveat": (
            ""
            if enough
            else f"Only {total_labelled} human-labelled decisions so far. These rates "
                 f"are indicative and should not be quoted as performance until the "
                 f"sample passes {MIN_SAMPLE_FOR_CONFIDENCE}."
        ),
        "audit_sample": {
            "responses_audited": len(audit_rows),
            "misses_found": audit_misses,
            "note": (
                "Responses Ring 0 allowed that were deep-checked anyway, so that "
                "misses can be counted instead of assumed to be zero."
            ),
        },
        "traffic": {
            "total_responses": total_responses,
            "action_breakdown": action_counts,
            "flag_rate": round(flagged / total_responses, 4) if total_responses else 0.0,
        },
        "mean_time_to_human_verdict_seconds": round(mttr, 1) if mttr is not None else None,
        "threshold_adjustments_30d": n_adjustments,
    }


async def per_policy_report(session: AsyncSession) -> list[dict]:
    policies = (await session.execute(select(Policy))).scalars().all()
    out = []
    for p in policies:
        report = await trust_report(session, p.use_case)
        report["label"] = p.label
        report["flag_rate_slo"] = p.flag_rate_slo
        report["within_slo"] = report["traffic"]["flag_rate"] <= p.flag_rate_slo * 1.5
        out.append(report)
    return out

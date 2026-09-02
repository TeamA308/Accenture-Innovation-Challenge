from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LLMResponse(Base):
    """One model call, plus every verdict the control plane attached to it.

    This row *is* the audit record. Anything a reviewer or regulator needs to
    reconstruct a decision must be reachable from here.
    """

    __tablename__ = "llm_responses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)

    # --- request ------------------------------------------------------------
    session_id: Mapped[str] = mapped_column(String(64), index=True, default=_uuid)
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    use_case: Mapped[str] = mapped_column(String(64), index=True)
    context_docs: Mapped[list] = mapped_column(JSON, default=list)
    # Reversibility drives the action matrix: a draft a human reads is
    # reversible; a payment, outbound email or DB write is not.
    is_reversible: Mapped[bool] = mapped_column(Boolean, default=True)
    downstream_action: Mapped[str] = mapped_column(String(32), default="draft")

    # --- generation ---------------------------------------------------------
    model_provider: Mapped[str] = mapped_column(String(32))
    model_name: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text, default="")
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)

    # --- ring 0 -------------------------------------------------------------
    ring0_latency_us: Mapped[int] = mapped_column(Integer, default=0)
    ring0_signals: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    action: Mapped[str] = mapped_column(String(16), default="allow", index=True)
    action_reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Set when the response text was rewritten (PII/secret redaction only).
    redacted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- ring 1 -------------------------------------------------------------
    ring1_status: Mapped[str] = mapped_column(String(16), default="skipped", index=True)
    ring1_reason: Mapped[str] = mapped_column(String(64), default="not_sampled")
    ring1_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ring1_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ring1_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # --- final state --------------------------------------------------------
    final_action: Mapped[str] = mapped_column(String(16), default="allow", index=True)
    # open | gated | released | withheld -- the commit gate for irreversible work
    gate_state: Mapped[str] = mapped_column(String(16), default="open")
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    def to_summary(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "use_case": self.use_case,
            "prompt": self.prompt,
            "response_text": self.redacted_text or self.response_text,
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "tokens_used": self.tokens_used,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": self.latency_ms,
            "ring0_latency_us": self.ring0_latency_us,
            "confidence": round(self.confidence, 3),
            "action": self.action,
            "final_action": self.final_action,
            "action_reasons": self.action_reasons or [],
            "ring1_status": self.ring1_status,
            "ring1_reason": self.ring1_reason,
            "gate_state": self.gate_state,
            "is_reversible": self.is_reversible,
            "downstream_action": self.downstream_action,
            "reviewed": self.reviewed,
        }

    def to_detail(self) -> dict:
        d = self.to_summary()
        d.update(
            {
                "raw_response_text": self.response_text,
                "redacted_text": self.redacted_text,
                "context_docs": self.context_docs or [],
                "ring0_signals": self.ring0_signals or {},
                "ring1_result": self.ring1_result,
                "ring1_latency_ms": self.ring1_latency_ms,
                "ring1_cost_usd": round(self.ring1_cost_usd, 6),
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
            }
        )
        return d

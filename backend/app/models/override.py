from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.response import _uuid, utcnow


class Override(Base):
    """A human verdict on a machine verdict. The training signal for Ring 2."""

    __tablename__ = "overrides"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    response_id: Mapped[str] = mapped_column(String(36), index=True)
    use_case: Mapped[str] = mapped_column(String(64), index=True, default="")
    reviewer_id: Mapped[str] = mapped_column(String(64), default="reviewer@demo")

    # accept  = the machine was wrong to flag/block  -> false positive
    # reject  = the machine was right                -> true positive
    # edit    = partially right, human repaired it
    decision: Mapped[str] = mapped_column(String(16))
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which Ring 0 signal drove the original verdict. Tells the tuner which
    # threshold to move.
    driving_signal: Mapped[str] = mapped_column(String(32), default="unknown")
    machine_action: Mapped[str] = mapped_column(String(16), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "response_id": self.response_id,
            "use_case": self.use_case,
            "reviewer_id": self.reviewer_id,
            "decision": self.decision,
            "edited_text": self.edited_text,
            "notes": self.notes,
            "driving_signal": self.driving_signal,
            "machine_action": self.machine_action,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class ThresholdAdjustment(Base):
    """Every automatic threshold move, logged so it can be explained on stage."""

    __tablename__ = "threshold_adjustments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    policy_id: Mapped[str] = mapped_column(String(36), index=True)
    use_case: Mapped[str] = mapped_column(String(64), index=True)
    field_changed: Mapped[str] = mapped_column(String(48))
    old_value: Mapped[float] = mapped_column(Float)
    new_value: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text, default="")
    triggered_by_override_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "use_case": self.use_case,
            "field_changed": self.field_changed,
            "old_value": round(self.old_value, 4),
            "new_value": round(self.new_value, 4),
            "reason": self.reason,
            "triggered_by_override_id": self.triggered_by_override_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.response import utcnow


class Conversation(Base):
    """Risk carried across turns of one session.

    A single questionable answer is a nuisance. The same answer used as the
    premise for three follow-up turns, one of which triggers a payment, is an
    incident. We accumulate risk per session and let it gate later actions.
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    use_case: Mapped[str] = mapped_column(String(64), default="")
    turns: Mapped[int] = mapped_column(Integer, default=0)
    # 0..1 exponentially-decayed accumulation of per-turn risk.
    accumulated_risk: Mapped[float] = mapped_column(Float, default=0.0)
    flagged_turns: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def to_dict(self) -> dict:
        return {
            "session_id": self.id,
            "use_case": self.use_case,
            "turns": self.turns,
            "accumulated_risk": round(self.accumulated_risk, 3),
            "flagged_turns": self.flagged_turns,
            "history": self.history or [],
        }

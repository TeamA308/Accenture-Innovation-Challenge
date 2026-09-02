from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.response import _uuid, utcnow


class Policy(Base):
    """Per-use-case governance config.

    The whole point of the product: one checker, many risk appetites. Every
    threshold Ring 0 and Ring 1 use is read from here at request time, so
    changing a slider in the UI changes behaviour on the very next request.
    """

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    use_case: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")

    # Governance context. Regulation differs by geography and sector, so it is
    # a policy field rather than a hard-coded rule.
    jurisdiction: Mapped[str] = mapped_column(String(32), default="IN_DPDP")
    risk_tolerance: Mapped[str] = mapped_column(String(16), default="medium")
    latency_budget_ms: Mapped[int] = mapped_column(Integer, default=800)

    # Ring 1 economics -- oversight has to stay a rounding error.
    ring1_sample_rate: Mapped[float] = mapped_column(Float, default=0.075)
    ring1_spend_cap_pct: Mapped[float] = mapped_column(Float, default=3.0)

    # Ring 0 thresholds.
    pii_block_threshold: Mapped[float] = mapped_column(Float, default=0.85)
    grounding_flag_threshold: Mapped[float] = mapped_column(Float, default=0.55)
    uncertainty_flag_threshold: Mapped[float] = mapped_column(Float, default=0.55)
    cost_anomaly_z: Mapped[float] = mapped_column(Float, default=2.5)
    confidence_block_threshold: Mapped[float] = mapped_column(Float, default=0.25)

    # Alert-fatigue guard: the share of traffic we are willing to flag. The
    # tuner steers towards this instead of ratcheting thresholds forever.
    flag_rate_slo: Mapped[float] = mapped_column(Float, default=0.12)

    # Entity types that must never reach a user for this use case.
    blocked_entity_types: Mapped[list] = mapped_column(
        JSON,
        default=lambda: ["US_SSN", "CREDIT_CARD", "AADHAAR", "PAN_IN", "SECRET_KEY", "IBAN"],
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "use_case": self.use_case,
            "label": self.label,
            "description": self.description,
            "jurisdiction": self.jurisdiction,
            "risk_tolerance": self.risk_tolerance,
            "latency_budget_ms": self.latency_budget_ms,
            "ring1_sample_rate": self.ring1_sample_rate,
            "ring1_spend_cap_pct": self.ring1_spend_cap_pct,
            "pii_block_threshold": self.pii_block_threshold,
            "grounding_flag_threshold": self.grounding_flag_threshold,
            "uncertainty_flag_threshold": self.uncertainty_flag_threshold,
            "cost_anomaly_z": self.cost_anomaly_z,
            "confidence_block_threshold": self.confidence_block_threshold,
            "flag_rate_slo": self.flag_rate_slo,
            "blocked_entity_types": self.blocked_entity_types or [],
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


DEFAULT_POLICIES: list[dict] = [
    {
        "use_case": "customer_facing",
        "label": "Customer support assistant",
        "description": (
            "Answers reach an external customer with no human in between. "
            "Lowest risk tolerance, tightest latency budget, PII is a hard stop."
        ),
        "jurisdiction": "IN_DPDP",
        "risk_tolerance": "low",
        "latency_budget_ms": 400,
        "ring1_sample_rate": 0.10,
        "ring1_spend_cap_pct": 3.0,
        "pii_block_threshold": 0.60,
        "grounding_flag_threshold": 0.72,
        "uncertainty_flag_threshold": 0.40,
        "cost_anomaly_z": 2.0,
        "confidence_block_threshold": 0.30,
        "flag_rate_slo": 0.18,
        "blocked_entity_types": [
            "US_SSN", "CREDIT_CARD", "AADHAAR", "PAN_IN", "SECRET_KEY",
            "IBAN", "PHONE_NUMBER", "EMAIL_ADDRESS",
        ],
    },
    {
        "use_case": "internal_copilot",
        "label": "Internal knowledge copilot",
        "description": (
            "An employee reads the draft before acting. Errors are recoverable, "
            "so we annotate rather than block and sample deep checks lightly."
        ),
        "jurisdiction": "IN_DPDP",
        "risk_tolerance": "medium",
        "latency_budget_ms": 900,
        "ring1_sample_rate": 0.07,
        "ring1_spend_cap_pct": 3.0,
        "pii_block_threshold": 0.90,
        "grounding_flag_threshold": 0.45,
        "uncertainty_flag_threshold": 0.65,
        "cost_anomaly_z": 3.0,
        "confidence_block_threshold": 0.15,
        "flag_rate_slo": 0.25,
        "blocked_entity_types": ["US_SSN", "CREDIT_CARD", "AADHAAR", "SECRET_KEY"],
    },
    {
        "use_case": "decision_support_regulated",
        "label": "Regulated decision support",
        "description": (
            "Feeds a decision inside a regulated workflow (credit, claims, "
            "clinical). Highest scrutiny, every grey-zone case goes to Ring 1, "
            "irreversible actions are gated until a verdict exists."
        ),
        "jurisdiction": "EU_AI_ACT",
        "risk_tolerance": "very_low",
        "latency_budget_ms": 1500,
        "ring1_sample_rate": 1.0,
        "ring1_spend_cap_pct": 6.0,
        "pii_block_threshold": 0.55,
        "grounding_flag_threshold": 0.80,
        "uncertainty_flag_threshold": 0.35,
        "cost_anomaly_z": 2.0,
        "confidence_block_threshold": 0.35,
        "flag_rate_slo": 0.40,
        "blocked_entity_types": [
            "US_SSN", "CREDIT_CARD", "AADHAAR", "PAN_IN", "SECRET_KEY",
            "IBAN", "MEDICAL_RECORD", "PHONE_NUMBER", "EMAIL_ADDRESS",
        ],
    },
]

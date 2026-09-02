"""Runtime configuration for ControlPlane.ai.

Everything here has a working default so a clean clone runs with no .env file
at all. Set values in .env (or real environment variables) to override.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # ---- storage -----------------------------------------------------------
    # Default is a local SQLite file so the prototype needs no database server.
    # Point DATABASE_URL at postgresql+asyncpg://... to run against Postgres.
    database_url: str = f"sqlite+aiosqlite:///{(REPO_ROOT / 'data' / 'controlplane.db').as_posix()}"

    # Optional. When set, the event bus and the Ring 1 queue use Redis instead
    # of the in-process implementations. Not required for the demo.
    redis_url: str | None = None

    # ---- model providers ---------------------------------------------------
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # "mock" keeps the whole demo offline and deterministic. If a real key is
    # present the UI can switch providers per request at runtime.
    default_provider: Literal["mock", "openai", "anthropic"] = "mock"
    default_model: str = "controlplane-sim-1"
    # Ring 1 deliberately uses a smaller/cheaper verifier than Ring 0's model.
    judge_provider: Literal["mock", "openai", "anthropic"] = "mock"
    judge_model: str = "controlplane-sim-judge"

    # ---- detection engines -------------------------------------------------
    # deterministic = built-in recognizers only (fast, no downloads)
    # hybrid        = deterministic + Presidio NER when presidio is installed
    pii_engine: Literal["deterministic", "hybrid"] = "deterministic"
    # Optional neural entailment backend for grounding. Off by default: the
    # deterministic evidence matcher is faster and produces citable spans.
    nli_backend: Literal["none", "hhem"] = "none"

    # ---- server ------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173"

    # ---- demo ---------------------------------------------------------------
    seed_demo_data: bool = True
    # Number of synthetic historical interactions generated at first startup so
    # the dashboard, FinOps tiles and trust report are not empty on stage.
    demo_backfill_events: int = 240

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    if s.database_url.startswith("sqlite"):
        (REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()

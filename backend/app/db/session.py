"""Async SQLAlchemy engine + session factory.

SQLite by default (zero setup); switches to Postgres transparently when
DATABASE_URL points at postgresql+asyncpg.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


IS_SQLITE = settings.database_url.startswith("sqlite")

# SQLite defaults to one writer at a time and fails instantly on contention.
# Write-ahead logging plus a busy timeout lets concurrent requests queue rather
# than error, which is what the load simulator needs. Postgres needs none of it.
_connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

if IS_SQLITE:
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver setup
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # Import models so they register on Base.metadata before create_all.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

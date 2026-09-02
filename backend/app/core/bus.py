"""Event bus + Ring 1 job queue.

In-process asyncio implementation by default so the prototype has no broker
dependency. If REDIS_URL is set the same interface is backed by Redis
pub/sub + a Redis list, which is how it would run across many API replicas.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import settings

log = logging.getLogger("controlplane.bus")


class EventBus:
    """Fan-out of verdict updates to every connected dashboard."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._redis: Any | None = None
        self._recent: list[dict] = []

    async def connect(self) -> None:
        if not settings.redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
            log.info("event bus: redis at %s", settings.redis_url)
        except Exception as exc:  # pragma: no cover - optional path
            log.warning("redis unavailable (%s); falling back to in-process bus", exc)
            self._redis = None

    async def publish(self, event: dict) -> None:
        self._recent.append(event)
        del self._recent[:-50]
        if self._redis is not None:  # pragma: no cover - optional path
            with contextlib.suppress(Exception):
                await self._redis.publish("verdict_updates", json.dumps(event, default=str))
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


class JobQueue:
    """Ring 1 work queue. Ring 0 never waits on this."""

    def __init__(self) -> None:
        self._q: asyncio.Queue = asyncio.Queue()
        self._redis: Any | None = None

    async def connect(self) -> None:
        if not settings.redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore

            self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            await self._redis.ping()
        except Exception:  # pragma: no cover - optional path
            self._redis = None

    async def enqueue(self, payload: dict) -> None:
        if self._redis is not None:  # pragma: no cover - optional path
            with contextlib.suppress(Exception):
                await self._redis.rpush("ring1_jobs", json.dumps(payload))
                return
        await self._q.put(payload)

    async def dequeue(self) -> dict:
        if self._redis is not None:  # pragma: no cover - optional path
            with contextlib.suppress(Exception):
                item = await self._redis.blpop("ring1_jobs", timeout=1)
                if item:
                    return json.loads(item[1])
                return {}
        return await self._q.get()

    def qsize(self) -> int:
        return self._q.qsize()


bus = EventBus()
ring1_queue = JobQueue()

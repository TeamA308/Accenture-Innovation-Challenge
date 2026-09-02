"""ControlPlane.ai backend entry point."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import audit, demo, generate, metrics, policy, review
from app.core.bus import bus, ring1_queue
from app.core.config import REPO_ROOT, settings
from app.db.session import SessionLocal, init_db
from app.rings.ring1.worker import worker_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("controlplane")

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await bus.connect()
    await ring1_queue.connect()

    async with SessionLocal() as session:
        created = await policy.seed_default_policies(session)
        if created:
            log.info("seeded %d default policies", created)

    stop = asyncio.Event()
    worker = asyncio.create_task(worker_loop(stop))

    backfill: asyncio.Task | None = None
    if settings.seed_demo_data and settings.demo_backfill_events > 0:
        backfill = asyncio.create_task(_backfill())

    log.info("ControlPlane.ai ready on http://%s:%s", settings.host, settings.port)
    log.info("provider=%s model=%s pii_engine=%s db=%s",
             settings.default_provider, settings.default_model,
             settings.pii_engine, settings.database_url.split("///")[-1])
    try:
        yield
    finally:
        stop.set()
        worker.cancel()
        if backfill:
            backfill.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


async def _backfill() -> None:
    """Populate the dashboard with measured history on first run.

    Empty charts make a live demo look broken. This runs the real pipeline over
    synthetic traffic, so the numbers on screen are genuinely measured -- they
    just were not measured by the person watching.
    """
    from sqlalchemy import func, select

    from app.models import LLMResponse

    async with SessionLocal() as session:
        existing = (await session.execute(select(func.count(LLMResponse.id)))).scalar() or 0
    if existing >= 20:
        log.info("backfill skipped: %d responses already stored", existing)
        return

    await asyncio.sleep(0.4)
    log.info("backfilling %d synthetic interactions...", settings.demo_backfill_events)
    try:
        result = await demo.simulate(demo.SimulateBody(
            count=settings.demo_backfill_events, concurrency=10, spread_hours=18,
        ))
        log.info("backfill complete: %s", result["by_action"])
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("backfill failed; the app is still usable, the dashboard just starts empty")


app = FastAPI(
    title="ControlPlane.ai",
    version="1.0.0",
    description=(
        "A risk-adaptive oversight layer that sits between any language model "
        "and the business action it triggers."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for module in (generate, policy, review, audit, metrics, demo):
    app.include_router(module.router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": settings.default_provider,
        "model": settings.default_model,
        "pii_engine": settings.pii_engine,
        "redis": bool(settings.redis_url),
        "frontend_built": FRONTEND_DIST.exists(),
    }


@app.websocket("/ws/verdicts")
async def verdict_stream(ws: WebSocket):
    """Live verdict updates.

    A WebSocket is a connection the browser opens once and keeps open, so the
    server can push updates the moment they happen instead of the page asking
    "anything new?" every second. It is what makes a Ring 1 verdict land on the
    dashboard the instant it resolves.
    """
    await ws.accept()
    try:
        async with bus.subscribe() as queue:
            await ws.send_text(json.dumps({"type": "connected"}))
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except (asyncio.TimeoutError, TimeoutError):
                    await ws.send_text(json.dumps({"type": "ping"}))
                    continue
                await ws.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - a dropped socket must not log noise
        pass


# ---------------------------------------------------------------------------
# Serve the built frontend from the same origin, so the whole prototype is one
# process on one port. If it has not been built, say so instead of 404ing.
# ---------------------------------------------------------------------------
if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    async def no_frontend():
        return {
            "status": "backend running, frontend not built",
            "fix": "run `npm install && npm run build` in frontend/, or start the "
                   "dev server with `npm run dev` and open http://localhost:5173",
            "api_docs": "/docs",
        }

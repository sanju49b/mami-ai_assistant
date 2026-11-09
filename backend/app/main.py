"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload

Serves: the Telegram webhook, the WHOOP OAuth callback, the JSON API for the
web app, and (if built) the web app itself, all from one origin.
"""
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

import app.handlers  # noqa: F401  (registers job handlers)
from app.admin import router as admin_router
from app.api import router as api_router
from app.config import get_settings
from app.db import session_scope
from app.integrations.router import router as whoop_router
from app.jobs import run_due_jobs
from app.scheduler import ensure_jobs
from app.telegram.router import router as telegram_router
from app.timeutil import utc_now

log = logging.getLogger(__name__)
WEB_DIST = Path(__file__).resolve().parent.parent / "web_dist"


def worker_tick() -> dict:
    with session_scope() as session:
        ensure_jobs(session, utc_now())
    return run_due_jobs()


def _worker_loop(stop: threading.Event, poll_seconds: float) -> None:
    while not stop.is_set():
        try:
            worker_tick()
        except Exception:  # noqa: BLE001 - the loop must never die
            log.exception("worker loop error")
        stop.wait(poll_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    stop = threading.Event()
    worker = None
    if settings.run_worker:
        worker = threading.Thread(
            target=_worker_loop, args=(stop, settings.worker_poll_seconds), daemon=True
        )
        worker.start()
    yield
    stop.set()
    if worker:
        worker.join(timeout=10)


def create_app() -> FastAPI:
    app = FastAPI(title="Day Manager", lifespan=lifespan)
    app.include_router(telegram_router)
    app.include_router(whoop_router)
    app.include_router(api_router)
    app.include_router(admin_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        with session_scope() as session:
            session.execute(text("select 1"))
        return {"status": "ok"}

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()

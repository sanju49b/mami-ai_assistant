"""FastAPI application entry point.

Run locally:  uvicorn app.main:app --reload
"""
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import text

import app.handlers  # noqa: F401  (registers job handlers)
from app.config import get_settings
from app.db import session_scope
from app.jobs import run_due_jobs
from app.telegram.router import router as telegram_router

log = logging.getLogger(__name__)


def _worker_loop(stop: threading.Event, poll_seconds: float) -> None:
    while not stop.is_set():
        try:
            run_due_jobs()
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

    @app.get("/health")
    def health() -> dict[str, str]:
        with session_scope() as session:
            session.execute(text("select 1"))
        return {"status": "ok"}

    return app


app = create_app()

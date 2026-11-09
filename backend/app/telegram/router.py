"""The Telegram webhook endpoint.

A plain `def` (not `async def`) endpoint: FastAPI runs it in a thread pool, so
the blocking database work doesn't stall other requests.
"""
import hmac
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings, webhook_token
from app.db import get_session
from app.telegram.ingest import InvalidUpdate, handle_update

router = APIRouter(prefix="/telegram", tags=["telegram"])


def verify_secret(
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> None:
    secret = get_settings().telegram_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    # Constant-time comparison, so the secret can't be guessed from response timing.
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, webhook_token(secret)
    ):
        raise HTTPException(status_code=401, detail="bad secret")


@router.post("/webhook", dependencies=[Depends(verify_secret)])
def telegram_webhook(
    background: BackgroundTasks,
    update: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        status = handle_update(session, update)
    except InvalidUpdate as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if get_settings().run_worker:
        # Commit now so the queued reply is visible to the background run below,
        # which sends it within seconds instead of at the next worker poll.
        session.commit()
        background.add_task(_run_jobs_now)
    return {"ok": True, "status": status}


def _run_jobs_now() -> None:
    from app.jobs import run_due_jobs

    try:
        run_due_jobs()
    except Exception:  # noqa: BLE001 - the worker loop will retry
        import logging

        logging.getLogger("mami").exception("running jobs after webhook failed")

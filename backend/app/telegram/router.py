"""The Telegram webhook endpoint.

A plain `def` (not `async def`) endpoint: FastAPI runs it in a thread pool, so
the blocking database work doesn't stall other requests.
"""
import hmac
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException
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
    update: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    try:
        status = handle_update(session, update)
    except InvalidUpdate as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "status": status}

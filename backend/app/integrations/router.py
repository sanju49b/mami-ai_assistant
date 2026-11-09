"""WHOOP OAuth callback. The user starts the flow by sending /whoop to the bot."""
import html

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import jobs
from app.db import get_session
from app.integrations import whoop

router = APIRouter(prefix="/whoop", tags=["whoop"])


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title><body style='font-family:system-ui;padding:2rem;"
        f"max-width:32rem;margin:auto'><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p>",
        status_code=status,
    )


@router.get("/callback")
def whoop_callback(
    state: str = "",
    code: str = "",
    error: str = "",
    session: Session = Depends(get_session),
) -> HTMLResponse:
    if error:
        return _page("WHOOP not connected", f"WHOOP said: {error}", 400)
    if not state or not code:
        return _page("WHOOP not connected", "Missing code or state.", 400)
    try:
        user_id = whoop.finish_connect(session, state, code)
    except whoop.WhoopError as exc:
        return _page("WHOOP not connected", str(exc), 400)
    jobs.enqueue(session, "whoop_sync", dedupe_key=f"whoop_sync:connect:{state}", user_id=user_id,
                 payload={"days": 7, "announce": True})
    return _page("WHOOP connected", "All set. You can close this tab and go back to Telegram.")

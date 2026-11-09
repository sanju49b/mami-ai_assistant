"""Chat with Mami in the web app (local mode's replacement for Telegram).

POST /api/chat saves your message first, in its own commit, so it is stored
even if Claude is unreachable. Then Mami replies (same agent and tools as on
Telegram); the reply is saved as a message too. GET /api/chat returns the
conversation, including messages Mami sent on her own (morning brief, evening
check-in, reminders), and any proposals waiting for Approve / Reject.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.runner import run_agent
from app.api import current_user
from app.common import get_settings_row
from app.db import get_session
from app.handlers import deliver, lock_user
from app.integrations import whoop
from app.models import Message
from app.telegram.outbound import send_text
from app.timeutil import local_date, utc_now
from app.views import pending_approvals_view

router = APIRouter(prefix="/api", tags=["chat"])
log = logging.getLogger("mami")


def _view(m: Message) -> dict:
    return {"id": str(m.id), "role": m.role, "text": m.content,
            "at": m.created_at.isoformat() if m.created_at else None}


def conversation(session: Session, user_id: uuid.UUID, limit: int) -> dict:
    rows = session.execute(
        select(Message)
        .where(Message.user_id == user_id, Message.role.in_(("user", "assistant")))
        .order_by(Message.created_at.desc(), Message.role.desc())  # 'user' before 'assistant' on ties
        .limit(limit)
    ).scalars().all()
    return {"messages": [_view(m) for m in reversed(rows)],
            "approvals": pending_approvals_view(session, user_id, utc_now())}


@router.get("/chat")
def get_chat(limit: int = 100, user_id: uuid.UUID = Depends(current_user),
             session: Session = Depends(get_session)) -> dict:
    return conversation(session, user_id, min(max(limit, 1), 500))


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _friendly(exc: Exception) -> str:
    name = type(exc).__name__
    if name == "AuthenticationError":
        return "Claude didn't accept the API key. Check ANTHROPIC_API_KEY in backend/.env."
    if name in ("PermissionDeniedError", "RateLimitError") or "credit" in str(exc).lower():
        return "Claude refused the request: check your Claude account has credit and limits."
    if name in ("APIConnectionError", "APITimeoutError"):
        return "Couldn't reach Claude. Are you online?"
    return f"Mami hit a problem ({name}). Your message is saved; try again."


@router.post("/chat")
def post_chat(body: ChatIn, user_id: uuid.UUID = Depends(current_user),
              session: Session = Depends(get_session)) -> dict:
    text = body.text.strip()
    now = utc_now()
    s = get_settings_row(session, user_id)
    message = Message(user_id=user_id, role="user", kind="text", content=text, created_at=now,
                      local_date=local_date(now, s.timezone, s.day_end_hour))
    session.add(message)
    session.commit()  # stored before anything can go wrong

    try:
        lock_user(session, user_id)
        if text.lower() in ("/whoop", "connect whoop"):
            if whoop.is_configured():
                url = whoop.start_connect(session, user_id)
                reply = f"Open this to connect WHOOP (works once, for 15 minutes):\n{url}"
            else:
                reply = ("WHOOP isn't set up yet: add WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET "
                         "to backend/.env and restart.")
            send_text(session, user_id=user_id, chat_id=1, text=reply, kind="system",
                      dedupe_key=f"ack:{message.id}")
        else:
            result = run_agent(session, user_id, text, utc_now(), source_message_id=message.id)
            deliver(session, user_id, 1, result, key=f"ack:{message.id}")
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        if type(exc).__module__.startswith("anthropic"):
            log.error("Mami couldn't reply: %s", _friendly(exc))
        else:
            log.exception("chat reply failed")
        raise HTTPException(502, _friendly(exc)) from exc
    return conversation(session, user_id, 100)

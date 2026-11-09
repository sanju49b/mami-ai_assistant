"""Sending messages to you, with a record of everything sent.

`dedupe_key` makes sends idempotent: if a job is retried after a message was
already sent with the same key, it is not sent again.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import Message, OutboundMessage
from app.telegram.client import Buttons, get_telegram_client
from app.timeutil import local_date, utc_now


def already_sent(session: Session, dedupe_key: str) -> bool:
    return (
        session.execute(
            select(OutboundMessage.id).where(
                OutboundMessage.dedupe_key == dedupe_key, OutboundMessage.status == "sent"
            )
        ).first()
        is not None
    )


def send_text(
    session: Session,
    *,
    user_id: uuid.UUID,
    chat_id: int,
    text: str,
    kind: str = "reply",
    dedupe_key: str | None = None,
    buttons: Buttons | None = None,
) -> bool:
    """Send a message. Returns False if it was already sent (same dedupe_key).

    Raises if Telegram fails, so the calling job is retried.
    """
    if dedupe_key and already_sent(session, dedupe_key):
        return False
    telegram_message_id = get_telegram_client().send_message(chat_id, text, buttons)
    session.execute(
        insert(OutboundMessage)
        .values(
            user_id=user_id,
            chat_id=chat_id,
            kind=kind,
            text_body=text,
            dedupe_key=dedupe_key,
            telegram_message_id=telegram_message_id,
            status="sent",
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )
    session.add(
        Message(
            user_id=user_id,
            role="assistant",
            kind="text",
            content=text,
            telegram_message_id=telegram_message_id,
            local_date=local_date(utc_now()),
        )
    )
    return True

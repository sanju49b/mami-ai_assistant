"""Turn a raw Telegram update into stored data, safely.

Order matters:
1. Store the raw update first (duplicates are ignored by the unique update_id).
2. Work out who sent it. Only a linked Telegram account is served.
3. Save the message and queue a job to process it. The webhook itself does no
   slow work, so Telegram gets a fast 200 and never needs to retry.
"""
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import jobs
from app.models import AuditEvent, InboundUpdate, LinkCode, Message, TelegramLink, UserSettings
from app.timeutil import local_date, utc_now


class InvalidUpdate(ValueError):
    pass


HELP_TEXT = (
    "I'm Mami. Just talk to me like a person:\n"
    "• \"45 min DSA, £12 lunch, skipped gym, buy groceries tomorrow\"\n"
    "• \"plan my day\" / \"what am I missing this week?\"\n"
    "• \"actually only 30 minutes\" / \"undo\"\n"
    "• \"remember I focus best before 9\"\n"
    "Commands: /whoop to connect WHOOP, /help for this message.\n"
    "Voice notes: use your keyboard's dictation button instead."
)


def _sender_id(update: dict[str, Any]) -> int | None:
    for key in ("message", "edited_message", "callback_query"):
        part = update.get(key)
        if isinstance(part, dict) and isinstance(part.get("from"), dict):
            return part["from"].get("id")
    return None


def _try_link(session: Session, code: str, telegram_user_id: int, chat_id: int) -> uuid.UUID | None:
    """Link this Telegram account using a one-time code. Returns the user id or None."""
    now = utc_now()
    link_code = session.execute(
        select(LinkCode).where(LinkCode.code == code).with_for_update()
    ).scalar_one_or_none()
    if link_code is None or link_code.used_at is not None or link_code.expires_at < now:
        return None
    already = session.execute(
        select(TelegramLink).where(
            (TelegramLink.user_id == link_code.user_id)
            | (TelegramLink.telegram_user_id == telegram_user_id)
        )
    ).first()
    if already is not None:
        return None
    link_code.used_at = now
    link = TelegramLink(user_id=link_code.user_id, telegram_user_id=telegram_user_id, chat_id=chat_id)
    session.add(link)
    session.add(
        AuditEvent(
            user_id=link_code.user_id,
            entity_type="telegram_link",
            entity_id=str(telegram_user_id),
            action="link",
            actor="user",
            after={"telegram_user_id": telegram_user_id, "chat_id": chat_id},
        )
    )
    return link_code.user_id


def handle_update(session: Session, update: dict[str, Any]) -> str:
    """Store and route one update. Returns a short status (useful in tests and logs)."""
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        raise InvalidUpdate("update_id missing or not an integer")
    sender = _sender_id(update)

    inbound_id = session.execute(
        insert(InboundUpdate)
        .values(update_id=update_id, telegram_user_id=sender, payload=update)
        .on_conflict_do_nothing(index_elements=["update_id"])
        .returning(InboundUpdate.id)
    ).scalar_one_or_none()
    if inbound_id is None:
        return "duplicate"
    inbound = session.get(InboundUpdate, inbound_id)

    def finish(status: str) -> str:
        inbound.status = status
        inbound.processed_at = utc_now()
        return status

    callback = update.get("callback_query")
    if isinstance(callback, dict) and sender is not None:
        link = session.execute(
            select(TelegramLink).where(TelegramLink.telegram_user_id == sender)
        ).scalar_one_or_none()
        if link is None:
            return finish("ignored_unlinked")
        data = callback.get("data") or ""
        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "ap" and parts[2] in ("a", "r"):
            try:
                uuid.UUID(parts[1])
            except ValueError:
                return finish("bad_callback")
            jobs.enqueue(
                session,
                "decide_approval",
                dedupe_key=f"callback:{callback['id']}",
                user_id=link.user_id,
                payload={"approval_id": parts[1], "decision": parts[2],
                         "callback_query_id": callback["id"], "chat_id": link.chat_id},
            )
            return finish("approval_decision")
        return finish("bad_callback")

    message = update.get("message")
    if not isinstance(message, dict) or sender is None:
        return finish("unsupported")

    chat_id = message["chat"]["id"]
    text = message.get("text") or message.get("caption") or ""
    link = session.execute(
        select(TelegramLink).where(TelegramLink.telegram_user_id == sender)
    ).scalar_one_or_none()

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        if link is None and len(parts) == 2:
            user_id = _try_link(session, parts[1].strip(), sender, chat_id)
            if user_id is None:
                return finish("link_failed")
            jobs.enqueue(
                session,
                "send_text",
                dedupe_key=f"linked:{update_id}",
                user_id=user_id,
                payload={
                    "chat_id": chat_id,
                    "text": ("Linked. I'm Mami, your day manager. Tell me what you did, spent or plan "
                             "to do, in your own words. Send /help for examples."),
                },
            )
            return finish("linked")
        if link is None:
            return finish("ignored_unlinked")

    if link is None:
        # Strangers get no reply and nothing is processed.
        return finish("ignored_unlinked")

    command = text.strip().split()[0].lower() if text.strip().startswith("/") else None
    if command in ("/whoop", "/start", "/help"):
        if command == "/whoop":
            jobs.enqueue(session, "whoop_link", dedupe_key=f"whoop_link:{update_id}",
                         user_id=link.user_id, payload={"chat_id": chat_id})
        else:
            jobs.enqueue(session, "send_text", dedupe_key=f"help:{update_id}", user_id=link.user_id,
                         payload={"chat_id": chat_id, "text": HELP_TEXT})
        return finish("command")

    settings = session.get(UserSettings, link.user_id)
    tz = settings.timezone if settings else "Europe/London"
    day_end = settings.day_end_hour if settings else 4
    sent_at = datetime.fromtimestamp(message.get("date", utc_now().timestamp()), tz=timezone.utc)

    if "voice" in message:
        voice = message["voice"]
        kind = "voice"
        meta = {
            "file_id": voice.get("file_id"),
            "duration": voice.get("duration"),
            "mime_type": voice.get("mime_type"),
        }
    elif text:
        kind, meta = "text", {}
    else:
        kind, meta = "other", {"keys": sorted(k for k in message if k not in ("from", "chat"))}

    stored = Message(
        user_id=link.user_id,
        role="user",
        kind=kind,
        content=text,
        meta=meta,
        telegram_message_id=message.get("message_id"),
        inbound_update_id=inbound_id,
        local_date=local_date(sent_at, tz, day_end),
    )
    session.add(stored)
    session.flush()
    jobs.enqueue(
        session,
        "process_message",
        dedupe_key=f"process_message:{update_id}",
        user_id=link.user_id,
        payload={"message_id": str(stored.id), "chat_id": chat_id},
    )
    return finish("stored")

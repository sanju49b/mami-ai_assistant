"""Proactive messages: deterministic gates first, AI second.

Before the assistant even thinks about starting a conversation, plain code
checks (in order): quiet hours, the daily cap, and the cooldown since the
last proactive message. Only if all pass is the message composed and sent.
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import get_settings_row
from app.jobs import Deferred
from app.models import OutboundMessage, TelegramLink, UserSettings
from app.telegram.client import Buttons
from app.telegram.outbound import send_text


@dataclass
class Gate:
    action: str  # send | defer | skip
    reason: str = ""
    until: datetime | None = None


def in_quiet_hours(local_t: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= local_t < end
    return local_t >= start or local_t < end  # wraps midnight, e.g. 22:30-07:00


def quiet_end_after(now: datetime, s: UserSettings) -> datetime:
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), s.quiet_end, tzinfo=tz)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(ZoneInfo("UTC"))


def check_gates(session: Session, user_id: uuid.UUID, now: datetime, *, important: bool = False) -> Gate:
    s = get_settings_row(session, user_id)
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    if in_quiet_hours(local.time(), s.quiet_start, s.quiet_end):
        return Gate("defer", "quiet hours", quiet_end_after(now, s))
    day_start = datetime.combine(local.date(), time(0), tzinfo=tz)
    sent_today = session.execute(
        select(func.count()).select_from(OutboundMessage).where(
            OutboundMessage.user_id == user_id,
            OutboundMessage.kind == "proactive",
            OutboundMessage.created_at >= day_start,
        )
    ).scalar_one()
    if sent_today >= s.max_proactive_per_day and not important:
        return Gate("skip", "daily cap reached")
    if not important:
        last = session.execute(
            select(func.max(OutboundMessage.created_at)).where(
                OutboundMessage.user_id == user_id, OutboundMessage.kind == "proactive"
            )
        ).scalar_one()
        if last is not None and now - last < timedelta(minutes=s.proactive_cooldown_minutes):
            return Gate("defer", "cooldown", last + timedelta(minutes=s.proactive_cooldown_minutes))
    return Gate("send")


def require_gate(session: Session, user_id: uuid.UUID, now: datetime, *, important: bool = False,
                 stale_after: datetime | None = None) -> bool:
    """Returns True to proceed, False to skip. Raises Deferred to retry later.

    stale_after: if the deferred time would be after this, skip instead (e.g. a
    morning brief is pointless in the afternoon).
    """
    gate = check_gates(session, user_id, now, important=important)
    if gate.action == "send":
        return True
    if gate.action == "defer":
        if stale_after is not None and gate.until and gate.until > stale_after:
            return False
        raise Deferred(gate.until, gate.reason)
    return False


def chat_id_for(session: Session, user_id: uuid.UUID) -> int | None:
    link = session.execute(
        select(TelegramLink).where(TelegramLink.user_id == user_id)
    ).scalar_one_or_none()
    return link.chat_id if link else None


def send_proactive(
    session: Session, user_id: uuid.UUID, text: str, dedupe_key: str,
    buttons: Buttons | None = None,
) -> bool:
    chat_id = chat_id_for(session, user_id)
    if chat_id is None:
        return False
    return send_text(session, user_id=user_id, chat_id=chat_id, text=text, kind="proactive",
                     dedupe_key=dedupe_key, buttons=buttons)

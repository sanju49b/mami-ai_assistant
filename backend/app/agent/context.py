"""What the agent knows on each turn (short-term + structured memory).

The structured database is the source of truth; this just packs the relevant
slice into the prompt, including record ids so references like "that" or
"move it to tomorrow" can be resolved.
"""
import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import jsonable, today_for
from app.models import ActivitySession, Expense, Memory, Message, UserSettings
from app.views import (
    activity_view,
    day_view,
    expense_view,
    goals_view,
    pending_approvals_view,
)

HISTORY_LIMIT = 30


def build_context(session: Session, user_id: uuid.UUID, settings: UserSettings, now: datetime) -> str:
    today = today_for(settings, now)
    tz = settings.timezone
    from zoneinfo import ZoneInfo

    local_now = now.astimezone(ZoneInfo(tz))
    recent_acts = session.execute(
        select(ActivitySession)
        .where(
            ActivitySession.user_id == user_id,
            ActivitySession.deleted_at.is_(None),
            ActivitySession.local_date >= today - timedelta(days=7),
        )
        .order_by(ActivitySession.local_date.desc(), ActivitySession.created_at.desc())
        .limit(25)
    ).scalars()
    recent_exp = session.execute(
        select(Expense)
        .where(
            Expense.user_id == user_id,
            Expense.deleted_at.is_(None),
            Expense.local_date >= today - timedelta(days=3),
        )
        .order_by(Expense.created_at.desc())
        .limit(15)
    ).scalars()
    memories = session.execute(
        select(Memory)
        .where(Memory.user_id == user_id, Memory.deleted_at.is_(None),
               Memory.status.in_(("confirmed", "observed")))
        .order_by(Memory.created_at.desc())
        .limit(40)
    ).scalars()
    confirmed, observed = [], []
    for m in memories:
        (confirmed if m.status == "confirmed" else observed).append(
            {"id": str(m.id), "content": m.content}
        )
    state = {
        "now": local_now.strftime("%A %d %B %Y, %H:%M"),
        "today": today.isoformat(),
        "coaching_style": settings.coaching_style,
        "today_view": day_view(session, user_id, today, tz),
        "recent_activities_7d": [activity_view(a, tz) for a in recent_acts],
        "recent_expenses_3d": [expense_view(e) for e in recent_exp],
        "goals": goals_view(session, user_id, today),
        "pending_approvals": pending_approvals_view(session, user_id, now),
        "memories_confirmed": confirmed,
        "memories_observed_unconfirmed": observed,
    }
    return "<state>\n" + json.dumps(jsonable(state), ensure_ascii=False) + "\n</state>"


def history_messages(
    session: Session, user_id: uuid.UUID, exclude_id: uuid.UUID | None
) -> list[dict]:
    """Recent conversation as alternating user/assistant text turns."""
    rows = list(
        session.execute(
            select(Message)
            .where(Message.user_id == user_id, Message.role.in_(("user", "assistant")))
            .order_by(Message.created_at.desc())
            .limit(HISTORY_LIMIT)
        ).scalars()
    )[::-1]
    turns: list[dict] = []
    for m in rows:
        if exclude_id is not None and m.id == exclude_id:
            continue
        text = m.content if m.kind == "text" else f"[{m.kind} message]"
        if not text:
            continue
        if turns and turns[-1]["role"] == m.role:
            turns[-1]["content"] += "\n" + text
        else:
            turns.append({"role": m.role, "content": text})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns

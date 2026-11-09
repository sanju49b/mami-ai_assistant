"""Closing a day and picking evening check-in questions.

Rule from the spec: missing information is never failure. At day close,
blocks and activities nobody reported on become 'unknown', never 'missed'.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.common import audit, get_settings_row, pence_to_str, row_dict
from app.config import get_settings
from app.llm import get_llm
from app.models import ActivitySession, DailyReview, Expense, Task, TimeBlock
from app.views import current_blocks, health_view


def close_day(session: Session, user_id: uuid.UUID, day: date, now: datetime) -> DailyReview:
    blocks = current_blocks(session, user_id, day)
    for b in blocks:
        if b.status in ("planned", "in_progress"):
            before = row_dict(b)
            b.status = "unknown"
            b.version += 1
            audit(session, user_id=user_id, entity=b, action="update", actor="system",
                  before=before, after=row_dict(b))
    acts = list(session.execute(
        select(ActivitySession).where(
            ActivitySession.user_id == user_id,
            ActivitySession.local_date == day,
            ActivitySession.deleted_at.is_(None),
        )
    ).scalars())
    for a in acts:
        if a.status in ("planned", "in_progress"):
            a.status = "unknown"
            a.version += 1
    expenses = list(session.execute(
        select(Expense).where(Expense.user_id == user_id, Expense.local_date == day,
                              Expense.deleted_at.is_(None))
    ).scalars())
    tasks_done = session.execute(
        select(Task.title).where(Task.user_id == user_id, Task.status == "done",
                                 Task.completed_at.is_not(None))
    ).scalars().all()

    minutes = defaultdict(int)
    for a in acts:
        if a.status == "completed" and a.minutes:
            minutes[a.category] += a.minutes
    spend = defaultdict(int)
    for e in expenses:
        spend[e.category] += e.amount_pence
    block_counts = defaultdict(int)
    for b in blocks:
        block_counts[b.status] += 1
    stats = {
        "blocks": dict(block_counts),
        "completed_minutes_by_category": dict(minutes),
        "activities": [{"title": a.title, "status": a.status, "minutes": a.minutes,
                        "notes": a.notes} for a in acts],
        "spend_by_category_pence": dict(spend),
        "spend_total": pence_to_str(sum(spend.values())),
        "tasks_done_total": len(tasks_done),
        "health": health_view(session, user_id, day),
        "had_any_input": bool(acts or expenses),
    }
    summary = _summarise(session, user_id, day, stats)
    stmt = insert(DailyReview).values(user_id=user_id, local_date=day, stats=stats,
                                      summary=summary, finalized_at=now)
    session.execute(stmt.on_conflict_do_update(
        constraint="uq_review_day",
        set_={"stats": stmt.excluded.stats, "summary": stmt.excluded.summary,
              "finalized_at": stmt.excluded.finalized_at}))
    return session.execute(
        select(DailyReview).where(DailyReview.user_id == user_id, DailyReview.local_date == day)
    ).scalar_one()


def _summarise(session: Session, user_id: uuid.UUID, day: date, stats: dict) -> str | None:
    """A 2-3 sentence factual summary by the cheaper model. Optional: on any
    failure the day is still closed with its stats."""
    if not stats["had_any_input"] and not stats["blocks"]:
        return "No plan and nothing logged. Saved as a quiet day."
    if not get_settings().anthropic_api_key and not _llm_overridden():
        return None
    from app.agent.runner import record_usage

    model = get_settings().background_model
    try:
        resp = get_llm().create(
            model=model,
            system=[{"type": "text", "text": (
                "Summarise this day's records in 2-3 plain sentences. Facts only, from the data. "
                "'unknown' means not reported, not failed. No advice.")}],
            messages=[{"role": "user", "content": f"Date {day.isoformat()}: {stats}"}],
            max_tokens=300,
        )
        record_usage(session, user_id, model, "day_close", resp)
        return resp.text or None
    except Exception:  # noqa: BLE001
        return None


def _llm_overridden() -> bool:
    from app import llm

    return llm._llm is not None


def evening_questions(session: Session, user_id: uuid.UUID, day: date) -> list[TimeBlock]:
    """Up to 3 important or goal-linked blocks still without a report."""
    blocks = [b for b in current_blocks(session, user_id, day)
              if b.status in ("planned", "in_progress") and (b.important or b.goal_id)]
    return blocks[:3]


def evening_message(session: Session, user_id: uuid.UUID, day: date) -> str | None:
    blocks = evening_questions(session, user_id, day)
    if not blocks:
        return None
    tz = get_settings_row(session, user_id).timezone
    from app.common import to_local_hhmm

    lines = [f"• {b.title} ({to_local_hhmm(b.starts_at, tz)}-{to_local_hhmm(b.ends_at, tz)})"
             for b in blocks]
    return ("Quick evening check-in. Did these happen? A word each is enough "
            "(done / skipped / partly, plus minutes if you like):\n" + "\n".join(lines))

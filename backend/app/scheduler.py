"""Creates each user's daily jobs ahead of time. Safe to call every minute:
every job has a dedupe key, so nothing is ever created twice.

Daily rhythm (local time, configurable in user_settings):
  WHOOP sync    07:00, 09:00, 11:00, 15:00, 20:00 (if connected)
  morning brief 07:30
  evening check 21:30
  day close     at the day-end hour (04:00) after the day
  weekly review Sunday 19:00
  reminders     at each task's due time
"""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jobs
from app.common import get_settings_row, today_for
from app.models import IntegrationConnection, Task, TelegramLink

WHOOP_SYNC_TIMES = (time(7, 0), time(9, 0), time(11, 0), time(15, 0), time(20, 0))
WEEKLY_REVIEW_TIME = time(19, 0)


def _at(day, t: time, tz: str) -> datetime:
    return datetime.combine(day, t, tzinfo=ZoneInfo(tz)).astimezone(ZoneInfo("UTC"))


def ensure_jobs(session: Session, now: datetime) -> int:
    created = 0
    links = session.execute(select(TelegramLink)).scalars().all()
    for link in links:
        uid = link.user_id
        s = get_settings_row(session, uid)
        tz = s.timezone
        today = today_for(s, now)
        whoop_on = session.execute(
            select(IntegrationConnection.id).where(
                IntegrationConnection.user_id == uid, IntegrationConnection.provider == "whoop",
                IntegrationConnection.status == "active")
        ).first() is not None
        for day in (today, today + timedelta(days=1)):
            d = day.isoformat()
            plan = [
                ("morning_brief", _at(day, s.morning_brief_time, tz), f"morning_brief:{d}"),
                ("evening_review", _at(day, s.evening_review_time, tz), f"evening_review:{d}"),
                ("day_close", _at(day + timedelta(days=1), time(s.day_end_hour, 5), tz),
                 f"day_close:{d}"),
            ]
            if whoop_on:
                plan += [("whoop_sync", _at(day, t, tz), f"whoop_sync:{d}:{t.hour}")
                         for t in WHOOP_SYNC_TIMES]
            if day.weekday() == 6:
                plan.append(("weekly_review", _at(day, WEEKLY_REVIEW_TIME, tz),
                             f"weekly_review:{day.isocalendar().year}-W{day.isocalendar().week}"))
            for kind, run_at, key in plan:
                # Don't create jobs that are already badly late (e.g. on first deploy
                # at night) except day_close, which must always run.
                if kind != "day_close" and run_at < now - timedelta(hours=2):
                    continue
                if jobs.enqueue(session, kind, key, payload={"date": d}, user_id=uid, run_at=run_at):
                    created += 1
        # Catch-up: if the laptop was off, close the days it missed (up to a week
        # back, never before Telegram was linked), so every day is still saved.
        linked_day = today_for(s, link.linked_at)
        for back in range(1, 8):
            day = today - timedelta(days=back)
            if day < linked_day:
                break
            if jobs.enqueue(session, "day_close", f"day_close:{day.isoformat()}",
                            payload={"date": day.isoformat()}, user_id=uid, run_at=now):
                created += 1
        for task in session.execute(
            select(Task).where(
                Task.user_id == uid, Task.status == "open", Task.due_at.is_not(None),
                Task.due_at > now, Task.due_at < now + timedelta(hours=48))
        ).scalars():
            key = f"reminder:{task.id}:{task.due_at.isoformat()}"
            if jobs.enqueue(session, "reminder", key, payload={"task_id": str(task.id)},
                            user_id=uid, run_at=task.due_at):
                created += 1
    return created

"""Read models: what a day, a week or a goal looks like, built from the tables.

Used by the agent's read tools and by the web API, so both always agree.
"""
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.common import jsonable, pence_to_str, to_local_hhmm
from app.models import (
    ActivitySession,
    DailyPlan,
    DailyReview,
    Expense,
    Goal,
    IntegrationConnection,
    PendingApproval,
    PlanVersion,
    Task,
    TimeBlock,
    UserSettings,
    WhoopDay,
    WhoopWorkout,
)


def current_blocks(session: Session, user_id: uuid.UUID, day: date) -> list[TimeBlock]:
    plan = session.execute(
        select(DailyPlan).where(DailyPlan.user_id == user_id, DailyPlan.local_date == day)
    ).scalar_one_or_none()
    if plan is None or plan.current_version_id is None:
        return []
    return list(
        session.execute(
            select(TimeBlock)
            .where(TimeBlock.plan_version_id == plan.current_version_id)
            .order_by(TimeBlock.starts_at)
        ).scalars()
    )


def plan_info(session: Session, user_id: uuid.UUID, day: date) -> dict[str, Any] | None:
    plan = session.execute(
        select(DailyPlan).where(DailyPlan.user_id == user_id, DailyPlan.local_date == day)
    ).scalar_one_or_none()
    if plan is None or plan.current_version_id is None:
        return None
    version = session.get(PlanVersion, plan.current_version_id)
    count = session.execute(
        select(func.count()).select_from(PlanVersion).where(PlanVersion.plan_id == plan.id)
    ).scalar_one()
    return {"version_no": version.version_no, "versions": count, "note": version.note}


def block_view(b: TimeBlock, tz: str) -> dict[str, Any]:
    return {
        "id": str(b.id),
        "title": b.title,
        "category": b.category,
        "start": to_local_hhmm(b.starts_at, tz),
        "end": to_local_hhmm(b.ends_at, tz),
        "starts_at": b.starts_at.isoformat(),
        "ends_at": b.ends_at.isoformat(),
        "status": b.status,
        "important": b.important,
        "checklist": b.checklist,
        "task_id": str(b.task_id) if b.task_id else None,
        "goal_id": str(b.goal_id) if b.goal_id else None,
        "version": b.version,
    }


def activity_view(a: ActivitySession, tz: str) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "title": a.title,
        "category": a.category,
        "date": a.local_date.isoformat(),
        "status": a.status,
        "minutes": a.minutes,
        "start": to_local_hhmm(a.started_at, tz),
        "end": to_local_hhmm(a.ended_at, tz),
        "source": a.source,
        "notes": a.notes,
        "version": a.version,
    }


def expense_view(e: Expense) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "amount": pence_to_str(e.amount_pence),
        "amount_pence": e.amount_pence,
        "category": e.category,
        "merchant": e.merchant,
        "note": e.note,
        "date": e.local_date.isoformat(),
        "status": e.status,
        "worth_it": e.worth_it,
        "version": e.version,
    }


def task_view(t: Task, tz: str) -> dict[str, Any]:
    return {
        "id": str(t.id),
        "title": t.title,
        "kind": t.kind,
        "status": t.status,
        "due_date": t.due_date.isoformat() if t.due_date else None,
        "due_time": to_local_hhmm(t.due_at, tz),
        "important": t.important,
        "goal_id": str(t.goal_id) if t.goal_id else None,
        "version": t.version,
    }


def health_view(session: Session, user_id: uuid.UUID, day: date) -> dict[str, Any]:
    connected = session.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.user_id == user_id,
            IntegrationConnection.provider == "whoop",
            IntegrationConnection.status == "active",
        )
    ).scalar_one_or_none()
    if connected is None:
        return {"whoop": "not connected"}
    whoop = session.execute(
        select(WhoopDay)
        .where(WhoopDay.user_id == user_id, WhoopDay.local_date == day)
        .order_by(WhoopDay.cycle_start.desc())
        .limit(1)
    ).scalar_one_or_none()
    workouts = session.execute(
        select(WhoopWorkout).where(WhoopWorkout.user_id == user_id, WhoopWorkout.local_date == day)
    ).scalars()
    if whoop is None:
        return {"whoop": "connected, no data for this day yet (recovery appears after you wake)"}
    return {
        "source": "WHOOP (measured data)",
        "recovery_score": jsonable(whoop.recovery_score),
        "hrv_ms": jsonable(whoop.hrv_rmssd_milli),
        "resting_hr": jsonable(whoop.resting_heart_rate),
        "sleep_performance": jsonable(whoop.sleep_performance),
        "sleep_hours": jsonable(whoop.sleep_hours),
        "day_strain": jsonable(whoop.strain),
        "steps_so_far": whoop.step_count,
        "workouts": [
            {"sport": w.sport_name, "strain": jsonable(w.strain), "avg_hr": w.average_heart_rate}
            for w in workouts
        ],
        "last_synced": connected.last_synced_at.isoformat() if connected.last_synced_at else None,
    }


def day_view(session: Session, user_id: uuid.UUID, day: date, tz: str) -> dict[str, Any]:
    activities = session.execute(
        select(ActivitySession)
        .where(
            ActivitySession.user_id == user_id,
            ActivitySession.local_date == day,
            ActivitySession.deleted_at.is_(None),
        )
        .order_by(ActivitySession.created_at)
    ).scalars()
    expenses = list(
        session.execute(
            select(Expense)
            .where(Expense.user_id == user_id, Expense.local_date == day, Expense.deleted_at.is_(None))
            .order_by(Expense.created_at)
        ).scalars()
    )
    tasks = session.execute(
        select(Task)
        .where(
            Task.user_id == user_id,
            (Task.due_date == day) | (and_(Task.status == "open", Task.due_date.is_(None))),
        )
        .order_by(Task.important.desc(), Task.created_at)
        .limit(30)
    ).scalars()
    review = session.execute(
        select(DailyReview).where(DailyReview.user_id == user_id, DailyReview.local_date == day)
    ).scalar_one_or_none()
    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "plan": plan_info(session, user_id, day),
        "blocks": [block_view(b, tz) for b in current_blocks(session, user_id, day)],
        "activities": [activity_view(a, tz) for a in activities],
        "expenses": [expense_view(e) for e in expenses],
        "spent_total": pence_to_str(sum(e.amount_pence for e in expenses)),
        "tasks": [task_view(t, tz) for t in tasks],
        "health": health_view(session, user_id, day),
        "review": {"summary": review.summary, "stats": review.stats} if review else None,
    }


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())  # Monday
    return start, start + timedelta(days=6)


def goal_progress(session: Session, goal: Goal, start: date, end: date) -> dict[str, Any]:
    """Measured progress for a target, from recorded data only."""
    actual: float | None = None
    if goal.metric in ("minutes", "sessions"):
        cond = [
            ActivitySession.user_id == goal.user_id,
            ActivitySession.local_date.between(start, end),
            ActivitySession.status == "completed",
            ActivitySession.deleted_at.is_(None),
        ]
        match = ActivitySession.goal_id == goal.id
        if goal.category:
            match = match | (ActivitySession.category == goal.category)
        cond.append(match)
        if goal.metric == "minutes":
            actual = session.execute(
                select(func.coalesce(func.sum(ActivitySession.minutes), 0)).where(*cond)
            ).scalar_one()
        else:
            actual = session.execute(
                select(func.count()).select_from(ActivitySession).where(*cond)
            ).scalar_one()
    elif goal.metric == "pence":
        cond = [
            Expense.user_id == goal.user_id,
            Expense.local_date.between(start, end),
            Expense.deleted_at.is_(None),
        ]
        if goal.category:
            cond.append(Expense.category == goal.category)
        actual = session.execute(
            select(func.coalesce(func.sum(Expense.amount_pence), 0)).where(*cond)
        ).scalar_one()
    elif goal.metric == "count":
        actual = session.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.goal_id == goal.id,
                Task.status == "done",
                func.date(Task.completed_at).between(start, end),
            )
        ).scalar_one()
    target = float(goal.target_value) if goal.target_value is not None else None
    return {
        "actual": float(actual) if actual is not None else None,
        "target": target,
        "percent": round(100 * float(actual) / target) if actual is not None and target else None,
        "period": f"{start.isoformat()}..{end.isoformat()}",
    }


def goals_view(session: Session, user_id: uuid.UUID, today: date) -> list[dict[str, Any]]:
    goals = session.execute(
        select(Goal).where(Goal.user_id == user_id, Goal.status == "active").order_by(Goal.created_at)
    ).scalars()
    start, end = week_bounds(today)
    out = []
    for g in goals:
        item = {
            "id": str(g.id),
            "kind": g.kind,
            "title": g.title,
            "category": g.category,
            "metric": g.metric,
            "target": float(g.target_value) if g.target_value is not None else None,
            "period": g.period,
            "deadline": g.deadline.isoformat() if g.deadline else None,
            "parent_id": str(g.parent_id) if g.parent_id else None,
            "version": g.version,
        }
        if g.kind == "weekly_target" and g.metric:
            item["this_week"] = goal_progress(session, g, start, end)
        out.append(item)
    return out


def week_view(session: Session, user_id: uuid.UUID, day: date, tz: str) -> dict[str, Any]:
    start, end = week_bounds(day)
    acts = session.execute(
        select(ActivitySession).where(
            ActivitySession.user_id == user_id,
            ActivitySession.local_date.between(start, end),
            ActivitySession.deleted_at.is_(None),
        )
    ).scalars()
    by_day: dict[str, dict[str, Any]] = defaultdict(lambda: defaultdict(int))
    for a in acts:
        if a.status == "completed" and a.minutes:
            by_day[a.local_date.isoformat()][a.category] += a.minutes
    spend = session.execute(
        select(Expense.category, func.sum(Expense.amount_pence))
        .where(
            Expense.user_id == user_id,
            Expense.local_date.between(start, end),
            Expense.deleted_at.is_(None),
        )
        .group_by(Expense.category)
    ).all()
    return {
        "week": f"{start.isoformat()}..{end.isoformat()}",
        "completed_minutes_by_day": {k: dict(v) for k, v in sorted(by_day.items())},
        "spend_by_category": {c: pence_to_str(p) for c, p in spend},
        "spend_total": pence_to_str(sum(p for _, p in spend)),
        "targets": [g for g in goals_view(session, user_id, day) if g["kind"] == "weekly_target"],
    }


def pending_approvals_view(session: Session, user_id: uuid.UUID, now: datetime) -> list[dict]:
    rows = session.execute(
        select(PendingApproval)
        .where(
            PendingApproval.user_id == user_id,
            PendingApproval.status == "pending",
            PendingApproval.expires_at > now,
        )
        .order_by(PendingApproval.created_at)
    ).scalars()
    return [
        {"id": str(a.id), "action": a.action_type, "summary": a.summary, "created_at": a.created_at.isoformat()}
        for a in rows
    ]


def settings_view(s: UserSettings) -> dict[str, Any]:
    return {
        "timezone": s.timezone,
        "day_end_hour": s.day_end_hour,
        "quiet_hours": f"{s.quiet_start.strftime('%H:%M')}-{s.quiet_end.strftime('%H:%M')}",
        "coaching_style": s.coaching_style,
    }

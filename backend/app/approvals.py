"""Running approved actions: exactly once, and only on the version the user saw.

decide() claims the approval atomically (UPDATE ... WHERE status='pending'),
so a double tap or a duplicate Telegram callback can never run it twice. If
the target record changed since the proposal, the approval is marked 'stale'
and nothing happens.
"""
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.common import audit, get_settings_row, local_dt, row_dict
from app.models import (
    ActivitySession,
    DailyPlan,
    Expense,
    Goal,
    InboxItem,
    Memory,
    PendingApproval,
    PlanVersion,
    Task,
    TimeBlock,
)
from app.views import current_blocks


class Stale(RuntimeError):
    pass


def decide(
    session: Session, user_id: uuid.UUID, approval_id: uuid.UUID, decision: str, now: datetime
) -> dict[str, Any]:
    """decision: 'approve' or 'reject'. Returns {status, message}."""
    approval = session.get(PendingApproval, approval_id)
    if approval is None or approval.user_id != user_id:
        return {"status": "not_found", "message": "I couldn't find that proposal."}
    if approval.status != "pending":
        return {"status": approval.status, "message": f"Already handled ({approval.status})."}
    if approval.expires_at <= now:
        approval.status = "expired"
        return {"status": "expired", "message": "That proposal expired. Ask me again if you still want it."}

    new_status = "executing" if decision == "approve" else "rejected"
    claimed = session.execute(
        update(PendingApproval)
        .where(PendingApproval.id == approval_id, PendingApproval.status == "pending")
        .values(status=new_status, decided_at=now)
        .returning(PendingApproval.id)
    ).scalar_one_or_none()
    if claimed is None:
        return {"status": "already_handled", "message": "Already handled."}
    session.refresh(approval)
    if decision != "approve":
        audit(session, user_id=user_id, entity=approval, action="reject", actor="user")
        return {"status": "rejected", "message": f"Rejected. Nothing changed.\n{approval.summary}"}

    try:
        with session.begin_nested():
            result = EXECUTORS[approval.action_type](session, approval, now)
        approval.status = "executed"
        approval.result = result
        audit(session, user_id=user_id, entity=approval, action="approve_execute", actor="user",
              after=result)
        return {"status": "executed", "message": f"Done: {approval.summary}", "result": result}
    except Stale as exc:
        approval.status = "stale"
        approval.result = {"error": str(exc)}
        return {"status": "stale", "message": f"Not applied: {exc} Ask me to propose it again."}
    except Exception as exc:  # noqa: BLE001
        approval.status = "failed"
        approval.result = {"error": str(exc)[:500]}
        return {"status": "failed", "message": "Something went wrong applying that. Nothing changed."}


def _check(obj, approval: PendingApproval) -> None:
    if obj is None or obj.user_id != approval.user_id:
        raise Stale("the record no longer exists.")
    if approval.target_version is not None and getattr(obj, "version", None) != approval.target_version:
        raise Stale("it was changed after I proposed this.")


# --- plans -------------------------------------------------------------------


def _get_or_create_plan(session: Session, user_id: uuid.UUID, day: date) -> DailyPlan:
    plan = session.execute(
        select(DailyPlan).where(DailyPlan.user_id == user_id, DailyPlan.local_date == day)
        .with_for_update()
    ).scalar_one_or_none()
    if plan is None:
        plan = DailyPlan(user_id=user_id, local_date=day)
        session.add(plan)
        session.flush()
    return plan


def new_plan_version(
    session: Session, user_id: uuid.UUID, day: date, blocks: list[dict], note: str | None,
    now: datetime,
) -> PlanVersion:
    """Create a new approved version. Earlier versions are kept untouched."""
    plan = _get_or_create_plan(session, user_id, day)
    previous = {(b.title.lower(), b.category): b for b in current_blocks(session, user_id, day)}
    max_no = session.execute(
        select(func.coalesce(func.max(PlanVersion.version_no), 0)).where(PlanVersion.plan_id == plan.id)
    ).scalar_one()
    version = PlanVersion(plan_id=plan.id, version_no=max_no + 1, note=note, approved_at=now)
    session.add(version)
    session.flush()
    for b in blocks:
        prior = previous.get((b["title"].lower(), b["category"]))
        session.add(
            TimeBlock(
                user_id=user_id,
                plan_version_id=version.id,
                title=b["title"],
                category=b["category"],
                starts_at=datetime.fromisoformat(b["starts_at"]),
                ends_at=datetime.fromisoformat(b["ends_at"]),
                important=b.get("important", False),
                # Keep what already happened (e.g. a block already marked completed).
                status=prior.status if prior is not None else "planned",
                checklist=prior.checklist if prior is not None and not b.get("checklist")
                else b.get("checklist", []),
                task_id=uuid.UUID(b["task_id"]) if b.get("task_id") else None,
                goal_id=uuid.UUID(b["goal_id"]) if b.get("goal_id") else None,
            )
        )
    plan.current_version_id = version.id
    session.flush()
    return version


def _block_dict(b: TimeBlock, **override) -> dict:
    d = {
        "title": b.title, "category": b.category, "starts_at": b.starts_at.isoformat(),
        "ends_at": b.ends_at.isoformat(), "important": b.important, "checklist": b.checklist,
        "task_id": str(b.task_id) if b.task_id else None,
        "goal_id": str(b.goal_id) if b.goal_id else None,
    }
    d.update(override)
    return d


def _overlaps(blocks: list[dict]) -> str | None:
    spans = sorted((datetime.fromisoformat(b["starts_at"]), datetime.fromisoformat(b["ends_at"]),
                    b["title"]) for b in blocks)
    for (s1, e1, t1), (s2, _e2, t2) in zip(spans, spans[1:]):
        if s2 < e1:
            return f"'{t1}' would overlap '{t2}'."
    return None


def apply_plan(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    day = date.fromisoformat(p["date"])
    version = new_plan_version(session, approval.user_id, day, p["blocks"], p.get("note"), now)
    return {"date": p["date"], "version_no": version.version_no, "blocks": len(p["blocks"])}


def reschedule_block(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    block = session.get(TimeBlock, uuid.UUID(p["block_id"]), with_for_update=True)
    _check(block, approval)
    settings = get_settings_row(session, approval.user_id)
    plan = session.execute(select(DailyPlan).join(PlanVersion, PlanVersion.plan_id == DailyPlan.id)
                           .where(PlanVersion.id == block.plan_version_id)).scalar_one()
    if plan.current_version_id != block.plan_version_id:
        raise Stale("the plan was revised after I proposed this.")
    source_day = plan.local_date
    target_day = date.fromisoformat(p["new_date"]) if p.get("new_date") else source_day
    duration = block.ends_at - block.starts_at
    tz, deh = settings.timezone, settings.day_end_hour
    start = (local_dt(target_day, p["new_start_time"], tz, deh) if p.get("new_start_time")
             else block.starts_at + timedelta(days=(target_day - source_day).days))
    end = (local_dt(target_day, p["new_end_time"], tz, deh) if p.get("new_end_time")
           else start + duration)
    moved = _block_dict(block, starts_at=start.isoformat(), ends_at=end.isoformat())
    others = [_block_dict(b) for b in current_blocks(session, approval.user_id, source_day)
              if b.id != block.id]
    if target_day == source_day:
        new_blocks = others + [moved]
        if (msg := _overlaps(new_blocks)):
            raise Stale(msg)
        new_plan_version(session, approval.user_id, source_day, new_blocks, "Rescheduled a block", now)
    else:
        target_blocks = [_block_dict(b) for b in current_blocks(session, approval.user_id, target_day)]
        if (msg := _overlaps(target_blocks + [moved])):
            raise Stale(msg)
        new_plan_version(session, approval.user_id, source_day, others,
                         f"Moved '{block.title}' to {target_day.isoformat()}", now)
        new_plan_version(session, approval.user_id, target_day, target_blocks + [moved],
                         f"Moved in '{block.title}'", now)
    return {"moved": block.title, "to": target_day.isoformat()}


def reschedule_task(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    task = session.get(Task, uuid.UUID(p["task_id"]), with_for_update=True)
    _check(task, approval)
    settings = get_settings_row(session, approval.user_id)
    before = row_dict(task)
    task.due_date = date.fromisoformat(p["new_date"]) if p.get("new_date") else None
    task.due_at = (local_dt(task.due_date, p["new_time"], settings.timezone, settings.day_end_hour)
                   if p.get("new_time") and task.due_date else None)
    task.version += 1
    task.updated_at = now
    audit(session, user_id=task.user_id, entity=task, action="update", actor="user",
          before=before, after=row_dict(task))
    return {"task": task.title, "due_date": p.get("new_date")}


# --- goals -------------------------------------------------------------------


def create_goal(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    goal = Goal(
        user_id=approval.user_id,
        kind=p["kind"],
        title=p["title"],
        category=(p.get("category") or None) and p["category"].lower(),
        metric=p.get("metric"),
        target_value=p.get("target_value"),
        period=p.get("period"),
        deadline=date.fromisoformat(p["deadline"]) if p.get("deadline") else None,
        parent_id=uuid.UUID(p["parent_id"]) if p.get("parent_id") else None,
        approved_at=now,
    )
    session.add(goal)
    session.flush()
    audit(session, user_id=goal.user_id, entity=goal, action="create", actor="user",
          after=row_dict(goal))
    return {"goal_id": str(goal.id), "title": goal.title}


def update_goal(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    goal = session.get(Goal, uuid.UUID(p["goal_id"]), with_for_update=True)
    _check(goal, approval)
    before = row_dict(goal)
    for key, value in p["changes"].items():
        if key == "deadline" and value:
            value = date.fromisoformat(value)
        setattr(goal, key, value)
    goal.version += 1
    goal.updated_at = now
    goal.approved_at = now
    audit(session, user_id=goal.user_id, entity=goal, action="update", actor="user",
          before=before, after=row_dict(goal))
    return {"goal_id": str(goal.id), "changes": p["changes"]}


DELETABLE = {"activity": ActivitySession, "expense": Expense, "task": Task, "note": InboxItem,
             "goal": Goal}


def delete_record(session: Session, approval: PendingApproval, now: datetime) -> dict:
    p = approval.payload
    obj = session.get(DELETABLE[p["entity_type"]], uuid.UUID(p["id"]), with_for_update=True)
    _check(obj, approval)
    before = row_dict(obj)
    if hasattr(obj, "deleted_at"):
        obj.deleted_at = now
    elif isinstance(obj, Task):
        obj.status = "cancelled"
    elif isinstance(obj, Goal):
        obj.status = "archived"
    if hasattr(obj, "version"):
        obj.version += 1
    audit(session, user_id=approval.user_id, entity=obj, action="delete", actor="user",
          before=before)
    return {"deleted": p["entity_type"], "id": p["id"]}


def confirm_memory(session: Session, approval: PendingApproval, now: datetime) -> dict:
    mem = session.get(Memory, uuid.UUID(approval.payload["memory_id"]), with_for_update=True)
    _check(mem, approval)
    before = row_dict(mem)
    mem.status = "confirmed"
    mem.confirmed_at = now
    mem.version += 1
    audit(session, user_id=mem.user_id, entity=mem, action="update", actor="user",
          before=before, after=row_dict(mem))
    return {"memory_id": str(mem.id)}


EXECUTORS = {
    "apply_plan": apply_plan,
    "reschedule_block": reschedule_block,
    "reschedule_task": reschedule_task,
    "create_goal": create_goal,
    "update_goal": update_goal,
    "delete_record": delete_record,
    "confirm_memory": confirm_memory,
}

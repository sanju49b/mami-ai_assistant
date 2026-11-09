"""JSON API for the web app.

Login: the web page asks for a code; the bot sends a 6-digit code to your
Telegram; the page sends it back and gets an httpOnly session cookie. No
passwords to manage, and only someone holding your Telegram can log in.
"""
import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import approvals, jobs
from app.common import (
    ToolError,
    audit,
    get_settings_row,
    jsonable,
    resolve_date,
    row_dict,
    today_for,
)
from app.config import get_settings
from app.db import get_session
from app.models import (
    ActivitySession,
    AuditEvent,
    Expense,
    FocusSession,
    Goal,
    InboxItem,
    LoginCode,
    Memory,
    Task,
    TelegramLink,
    TimeBlock,
    WebSession,
)
from app.timeutil import utc_now
from app.views import (
    day_view,
    expense_view,
    goals_view,
    pending_approvals_view,
    settings_view,
    task_view,
    week_view,
)

router = APIRouter(prefix="/api", tags=["web"])
COOKIE = "dm_session"
SESSION_DAYS = 30


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- auth --------------------------------------------------------------------


def current_user(
    dm_session: str | None = Cookie(default=None), session: Session = Depends(get_session)
) -> uuid.UUID:
    if not dm_session:
        raise HTTPException(401, "Not logged in")
    row = session.get(WebSession, _hash(dm_session))
    if row is None or row.expires_at < utc_now():
        raise HTTPException(401, "Session expired")
    return row.user_id


def _the_user(session: Session) -> uuid.UUID:
    links = session.execute(select(TelegramLink.user_id)).scalars().all()
    if len(links) != 1:
        raise HTTPException(400, "Link your Telegram account first")
    return links[0]


@router.post("/auth/request-code")
def request_code(session: Session = Depends(get_session)) -> dict[str, str]:
    user_id = _the_user(session)
    recent = session.execute(
        select(func.count()).select_from(LoginCode).where(
            LoginCode.user_id == user_id, LoginCode.expires_at > utc_now() - timedelta(minutes=50))
    ).scalar_one()
    if recent >= 5:
        raise HTTPException(429, "Too many codes requested. Try again in an hour.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    row = LoginCode(user_id=user_id, code_hash=_hash(f"{user_id}:{code}"),
                    expires_at=utc_now() + timedelta(minutes=10))
    session.add(row)
    session.flush()
    jobs.enqueue(session, "login_code", dedupe_key=f"login_code:{row.id}", user_id=user_id,
                 payload={"code": code})
    return {"status": "sent", "message": "Check Telegram for your code."}


class VerifyIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


@router.post("/auth/verify")
def verify_code(body: VerifyIn, response: Response, session: Session = Depends(get_session)):
    user_id = _the_user(session)
    row = session.execute(
        select(LoginCode)
        .where(LoginCode.user_id == user_id, LoginCode.used_at.is_(None),
               LoginCode.expires_at > utc_now())
        .order_by(LoginCode.expires_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(400, "No valid code. Request a new one.")
    row.attempts += 1
    if row.attempts > 5:
        row.used_at = utc_now()
        raise HTTPException(429, "Too many attempts. Request a new code.")
    if not hmac.compare_digest(row.code_hash, _hash(f"{user_id}:{body.code}")):
        # Commit the attempt count, then reject.
        session.commit()
        raise HTTPException(400, "Wrong code")
    row.used_at = utc_now()
    token = secrets.token_urlsafe(32)
    session.add(WebSession(token_hash=_hash(token), user_id=user_id,
                           expires_at=utc_now() + timedelta(days=SESSION_DAYS)))
    response.set_cookie(
        COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=get_settings().base_url.startswith("https"), path="/",
    )
    return {"status": "ok"}


@router.post("/auth/logout")
def logout(response: Response, dm_session: str | None = Cookie(default=None),
           session: Session = Depends(get_session)):
    if dm_session:
        row = session.get(WebSession, _hash(dm_session))
        if row:
            session.delete(row)
    response.delete_cookie(COOKIE, path="/")
    return {"status": "ok"}


# --- reads -------------------------------------------------------------------


@router.get("/me")
def me(user_id: uuid.UUID = Depends(current_user), session: Session = Depends(get_session)):
    from app.models import User

    s = get_settings_row(session, user_id)
    user = session.get(User, user_id)
    return {"user_id": str(user_id), "name": user.display_name if user else "",
            "today": today_for(s, utc_now()).isoformat(), "settings": settings_view(s)}


@router.get("/day")
def get_day(date: str | None = None, user_id: uuid.UUID = Depends(current_user),
            session: Session = Depends(get_session)):
    s = get_settings_row(session, user_id)
    try:
        day = resolve_date(date, s, utc_now())
    except ToolError as exc:
        raise HTTPException(400, str(exc)) from exc
    view = day_view(session, user_id, day, s.timezone)
    view["approvals"] = pending_approvals_view(session, user_id, utc_now())
    view["focus"] = _active_focus(session, user_id)
    return jsonable(view)


@router.get("/week")
def get_week(date: str | None = None, user_id: uuid.UUID = Depends(current_user),
             session: Session = Depends(get_session)):
    s = get_settings_row(session, user_id)
    return jsonable(week_view(session, user_id, resolve_date(date, s, utc_now()), s.timezone))


@router.get("/goals")
def get_goals(user_id: uuid.UUID = Depends(current_user), session: Session = Depends(get_session)):
    s = get_settings_row(session, user_id)
    return {"goals": goals_view(session, user_id, today_for(s, utc_now()))}


@router.get("/tasks")
def get_tasks(status: str = "open", user_id: uuid.UUID = Depends(current_user),
              session: Session = Depends(get_session)):
    s = get_settings_row(session, user_id)
    rows = session.execute(
        select(Task).where(Task.user_id == user_id, Task.status == status)
        .order_by(Task.due_date.nulls_last(), Task.created_at).limit(200)
    ).scalars()
    inbox = session.execute(
        select(InboxItem).where(InboxItem.user_id == user_id, InboxItem.deleted_at.is_(None))
        .order_by(InboxItem.created_at.desc()).limit(100)
    ).scalars()
    return {"tasks": [task_view(t, s.timezone) for t in rows],
            "inbox": [{"id": str(i.id), "kind": i.kind, "content": i.content,
                       "created_at": i.created_at.isoformat()} for i in inbox]}


@router.get("/expenses")
def get_expenses(days: int = 30, user_id: uuid.UUID = Depends(current_user),
                 session: Session = Depends(get_session)):
    s = get_settings_row(session, user_id)
    since = today_for(s, utc_now()) - timedelta(days=days)
    rows = list(session.execute(
        select(Expense).where(Expense.user_id == user_id, Expense.deleted_at.is_(None),
                              Expense.local_date > since)
        .order_by(Expense.local_date.desc(), Expense.created_at.desc())
    ).scalars())
    by_cat: dict[str, int] = {}
    for e in rows:
        by_cat[e.category] = by_cat.get(e.category, 0) + e.amount_pence
    return {"expenses": [expense_view(e) for e in rows], "by_category_pence": by_cat,
            "total_pence": sum(by_cat.values()), "days": days}


@router.get("/approvals")
def list_approvals(user_id: uuid.UUID = Depends(current_user), session: Session = Depends(get_session)):
    return {"approvals": pending_approvals_view(session, user_id, utc_now())}


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]


@router.post("/approvals/{approval_id}")
def decide(approval_id: uuid.UUID, body: DecisionIn, user_id: uuid.UUID = Depends(current_user),
           session: Session = Depends(get_session)):
    return approvals.decide(session, user_id, approval_id, body.decision, utc_now())


@router.get("/history")
def history(days: int = 7, user_id: uuid.UUID = Depends(current_user),
            session: Session = Depends(get_session)):
    rows = session.execute(
        select(AuditEvent).where(AuditEvent.user_id == user_id,
                                 AuditEvent.created_at > utc_now() - timedelta(days=days))
        .order_by(AuditEvent.created_at.desc()).limit(300)
    ).scalars()
    return {"events": [{"at": e.created_at.isoformat(), "entity": e.entity_type, "action": e.action,
                        "actor": e.actor, "before": e.before, "after": e.after} for e in rows]}


# --- edits from the web --------------------------------------------------------


def _owned(session: Session, model, obj_id: uuid.UUID, user_id: uuid.UUID):
    obj = session.get(model, obj_id, with_for_update=True)
    if obj is None or obj.user_id != user_id or getattr(obj, "deleted_at", None):
        raise HTTPException(404, "Not found")
    return obj


class BlockPatch(BaseModel):
    version: int
    status: Literal["planned", "in_progress", "completed", "missed", "cancelled", "unknown"] | None = None
    checklist: list[dict[str, Any]] | None = None


@router.patch("/blocks/{block_id}")
def patch_block(block_id: uuid.UUID, body: BlockPatch, user_id: uuid.UUID = Depends(current_user),
                session: Session = Depends(get_session)):
    block = _owned(session, TimeBlock, block_id, user_id)
    if block.version != body.version:
        raise HTTPException(409, "This block changed elsewhere. Refresh and try again.")
    before = row_dict(block)
    if body.status is not None:
        block.status = body.status
    if body.checklist is not None:
        block.checklist = [{"text": str(i.get("text", ""))[:200], "done": bool(i.get("done"))}
                           for i in body.checklist][:30]
    block.version += 1
    audit(session, user_id=user_id, entity=block, action="update", actor="user", before=before,
          after=row_dict(block))
    return {"status": "ok", "version": block.version}


class TaskPatch(BaseModel):
    version: int
    status: Literal["open", "done", "cancelled"]


@router.patch("/tasks/{task_id}")
def patch_task(task_id: uuid.UUID, body: TaskPatch, user_id: uuid.UUID = Depends(current_user),
               session: Session = Depends(get_session)):
    task = _owned(session, Task, task_id, user_id)
    if task.version != body.version:
        raise HTTPException(409, "This task changed elsewhere. Refresh and try again.")
    before = row_dict(task)
    task.status = body.status
    task.completed_at = utc_now() if body.status == "done" else None
    task.version += 1
    task.updated_at = utc_now()
    audit(session, user_id=user_id, entity=task, action="update", actor="user", before=before,
          after=row_dict(task))
    return {"status": "ok", "version": task.version}


@router.get("/memories")
def memories(user_id: uuid.UUID = Depends(current_user), session: Session = Depends(get_session)):
    rows = session.execute(
        select(Memory).where(Memory.user_id == user_id, Memory.deleted_at.is_(None))
        .order_by(Memory.status, Memory.created_at.desc())
    ).scalars()
    return {"memories": [{"id": str(m.id), "content": m.content, "kind": m.kind, "status": m.status,
                          "created_at": m.created_at.isoformat(), "version": m.version}
                         for m in rows]}


class MemoryPatch(BaseModel):
    version: int
    content: str | None = Field(None, min_length=1, max_length=500)
    status: Literal["confirmed", "observed"] | None = None


@router.patch("/memories/{memory_id}")
def patch_memory(memory_id: uuid.UUID, body: MemoryPatch, user_id: uuid.UUID = Depends(current_user),
                 session: Session = Depends(get_session)):
    mem = _owned(session, Memory, memory_id, user_id)
    if mem.version != body.version:
        raise HTTPException(409, "Changed elsewhere. Refresh.")
    before = row_dict(mem)
    if body.content is not None:
        mem.content = body.content
    if body.status is not None:
        mem.status = body.status
        mem.confirmed_at = utc_now() if body.status == "confirmed" else None
    mem.version += 1
    audit(session, user_id=user_id, entity=mem, action="update", actor="user", before=before,
          after=row_dict(mem))
    return {"status": "ok"}


@router.delete("/memories/{memory_id}")
def delete_memory(memory_id: uuid.UUID, user_id: uuid.UUID = Depends(current_user),
                  session: Session = Depends(get_session)):
    mem = _owned(session, Memory, memory_id, user_id)
    before = row_dict(mem)
    mem.deleted_at = utc_now()
    mem.status = "rejected"
    audit(session, user_id=user_id, entity=mem, action="delete", actor="user", before=before)
    return {"status": "ok"}


# --- focus timer -----------------------------------------------------------------


def _focused_seconds(f: FocusSession, now: datetime) -> int:
    end = f.ended_at or (f.paused_at if f.status == "paused" else now)
    return max(0, int((end - f.started_at).total_seconds()) - f.paused_seconds)


def _focus_view(f: FocusSession) -> dict[str, Any]:
    return {"id": str(f.id), "title": f.title, "category": f.category, "status": f.status,
            "work_minutes": f.work_minutes, "break_minutes": f.break_minutes,
            "started_at": f.started_at.isoformat(), "focused_seconds": _focused_seconds(f, utc_now()),
            "task_id": str(f.task_id) if f.task_id else None}


def _active_focus(session: Session, user_id: uuid.UUID) -> dict | None:
    f = session.execute(
        select(FocusSession).where(FocusSession.user_id == user_id,
                                   FocusSession.status.in_(("running", "paused")))
        .order_by(FocusSession.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    return _focus_view(f) if f else None


class FocusStart(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    category: str = "study"
    task_id: uuid.UUID | None = None
    goal_id: uuid.UUID | None = None
    work_minutes: int = Field(25, ge=5, le=180)
    break_minutes: int = Field(5, ge=0, le=60)


@router.post("/focus")
def focus_start(body: FocusStart, user_id: uuid.UUID = Depends(current_user),
                session: Session = Depends(get_session)):
    if _active_focus(session, user_id):
        raise HTTPException(409, "A focus session is already running")
    f = FocusSession(user_id=user_id, title=body.title, category=body.category.lower(),
                     task_id=body.task_id, goal_id=body.goal_id, work_minutes=body.work_minutes,
                     break_minutes=body.break_minutes, started_at=utc_now())
    session.add(f)
    session.flush()
    return _focus_view(f)


class FocusAction(BaseModel):
    action: Literal["pause", "resume", "complete", "stop"]


@router.post("/focus/{focus_id}")
def focus_action(focus_id: uuid.UUID, body: FocusAction, user_id: uuid.UUID = Depends(current_user),
                 session: Session = Depends(get_session)):
    f = _owned(session, FocusSession, focus_id, user_id)
    now = utc_now()
    if f.status in ("completed", "stopped"):
        # Idempotent: finishing twice never records the session twice.
        return _focus_view(f)
    if body.action == "pause" and f.status == "running":
        f.status, f.paused_at = "paused", now
    elif body.action == "resume" and f.status == "paused":
        f.paused_seconds += int((now - f.paused_at).total_seconds())
        f.status, f.paused_at = "running", None
    elif body.action in ("complete", "stop"):
        if f.status == "paused":
            f.paused_seconds += int((now - f.paused_at).total_seconds())
            f.paused_at = None
        f.ended_at = now
        f.status = "completed" if body.action == "complete" else "stopped"
        minutes = _focused_seconds(f, now) // 60
        if minutes >= 1:
            s = get_settings_row(session, user_id)
            act = ActivitySession(
                user_id=user_id, title=f.title, category=f.category,
                local_date=today_for(s, f.started_at), started_at=f.started_at, ended_at=now,
                minutes=minutes, status="completed", source="focus", task_id=f.task_id,
                goal_id=f.goal_id,
            )
            session.add(act)
            session.flush()
            f.activity_session_id = act.id
            audit(session, user_id=user_id, entity=act, action="create", actor="user",
                  after=row_dict(act))
    return _focus_view(f)


# --- settings and data export ------------------------------------------------------


class SettingsPatch(BaseModel):
    quiet_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    quiet_end: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    morning_brief_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    evening_review_time: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    max_proactive_per_day: int | None = Field(None, ge=0, le=20)
    monthly_ai_budget_pence: int | None = Field(None, ge=0, le=100_000)
    coaching_style: Literal["gentle", "firm", "adaptive"] | None = None


@router.patch("/settings")
def patch_settings(body: SettingsPatch, user_id: uuid.UUID = Depends(current_user),
                   session: Session = Depends(get_session)):
    from datetime import time as _time

    s = get_settings_row(session, user_id)
    before = row_dict(s)
    for key, value in body.model_dump(exclude_none=True).items():
        if key.endswith(("_start", "_end", "_time")):
            value = _time.fromisoformat(value)
        setattr(s, key, value)
    audit(session, user_id=user_id, entity_type="user_settings", entity_id=str(user_id),
          action="update", actor="user", before=before, after=row_dict(s))
    return {"status": "ok", "settings": settings_view(s)}


@router.get("/export")
def export(user_id: uuid.UUID = Depends(current_user), session: Session = Depends(get_session)):
    out: dict[str, Any] = {}
    for model in (Goal, Task, InboxItem, ActivitySession, Expense, Memory, TimeBlock, FocusSession):
        rows = session.execute(select(model).where(model.user_id == user_id)).scalars()
        out[model.__tablename__] = [row_dict(r) for r in rows]
    return out


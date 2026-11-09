"""The agent's tools.

Principle: "AI thinks; backend verifies." The model only *asks* to call a tool.
This module validates every input (pydantic), applies the rules from the spec
in plain code, writes the change with an audit record, and returns what really
happened. Three permission tiers:

- read:     look things up, never changes data
- auto:     safe, clear logging the user reported (undoable)
- approval: changes to commitments, goals, targets or deletions. These only
            create a PendingApproval; nothing changes until the user approves.
"""
import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.common import (
    Conflict,
    ToolError,
    audit,
    local_dt,
    pence_to_str,
    resolve_date,
    row_dict,
    today_for,
)
from app.models import (
    ActivitySession,
    AuditEvent,
    Expense,
    Goal,
    InboxItem,
    Memory,
    PendingApproval,
    Task,
    TimeBlock,
    UserSettings,
)
from app.views import (
    activity_view,
    day_view,
    expense_view,
    goals_view,
    health_view,
    task_view,
    week_view,
)

Tier = Literal["read", "auto", "approval"]

ActivityStatus = Literal["completed", "in_progress", "planned", "missed", "cancelled", "unknown"]
Category = str  # free text, but the prompt lists the usual ones


@dataclass
class ToolContext:
    session: Session
    user_id: uuid.UUID
    settings: UserSettings
    now: datetime
    source_message_id: uuid.UUID | None = None
    receipts: list[str] = field(default_factory=list)  # what was really saved
    approvals: list[PendingApproval] = field(default_factory=list)

    @property
    def tz(self) -> str:
        return self.settings.timezone

    @property
    def today(self):
        return today_for(self.settings, self.now)


@dataclass
class ToolSpec:
    name: str
    description: str
    model: type[BaseModel]
    tier: Tier
    fn: Callable[[ToolContext, Any], dict[str, Any]]


TOOLS: dict[str, ToolSpec] = {}


def tool(name: str, tier: Tier, description: str, model: type[BaseModel]):
    def register(fn):
        TOOLS[name] = ToolSpec(name, description, model, tier, fn)
        return fn

    return register


# --- JSON schema for Claude ------------------------------------------------


def _clean_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make pydantic's JSON schema suitable for Claude strict tools.

    - inline $refs (nested models)
    - drop schema annotations ('title', 'default') but NOT properties that
      happen to be called 'title'
    - drop numeric/length limits: pydantic still enforces them server-side
    - additionalProperties=false on every object
    """
    defs = schema.pop("$defs", {})
    drop = {"title", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
            "minItems", "maxItems", "minLength", "maxLength"}

    def walk(node: Any, is_properties: bool = False) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            if is_properties:  # keys here are field names, keep them all
                return {k: walk(v) for k, v in node.items()}
            out = {k: walk(v, is_properties=(k == "properties"))
                   for k, v in node.items() if k not in drop}
            if out.get("type") == "object":
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def tool_definitions() -> list[dict[str, Any]]:
    defs = []
    for spec in TOOLS.values():
        defs.append(
            {
                "name": spec.name,
                "description": f"[{spec.tier}] {spec.description}",
                "input_schema": _clean_schema(spec.model.model_json_schema()),
                "strict": True,
            }
        )
    # Cache the (stable) tool list: cache_control on the last tool.
    defs[-1]["cache_control"] = {"type": "ephemeral"}
    return defs


def execute(ctx: ToolContext, name: str, raw_input: dict[str, Any]) -> dict[str, Any]:
    """Validate and run one tool call. Raises ToolError for fixable problems."""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolError(f"Unknown tool {name!r}")
    try:
        args = spec.model.model_validate(raw_input)
    except ValidationError as exc:
        raise ToolError(f"Invalid input for {name}: {exc.errors(include_url=False)}") from exc
    # Each tool call runs in a savepoint: if it fails, its partial writes vanish
    # but earlier successful tool calls in the same turn are kept.
    with ctx.session.begin_nested():
        return spec.fn(ctx, args)


# --- helpers -----------------------------------------------------------------


def _uuid(value: str | None, what: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ToolError(f"{what} is not a valid id: {value!r}") from exc


def _owned(ctx: ToolContext, model, id_value: str, what: str):
    obj = ctx.session.get(model, _uuid(id_value, what), with_for_update=True)
    if obj is None or obj.user_id != ctx.user_id or getattr(obj, "deleted_at", None) is not None:
        raise ToolError(f"No {what} with id {id_value}")
    return obj


def _check_version(obj, expected: int | None) -> None:
    if expected is not None and obj.version != expected:
        raise Conflict(
            f"This record changed since you read it (version {obj.version}, expected {expected}). "
            "Re-read it and try again."
        )


def _bump(obj, now: datetime) -> None:
    obj.version += 1
    if hasattr(obj, "updated_at"):
        obj.updated_at = now


def _same_message_duplicate(ctx: ToolContext, model, **filters):
    """If this exact record was already created from this message (e.g. the job
    was retried), return it instead of creating a duplicate."""
    if ctx.source_message_id is None:
        return None
    cond = [model.user_id == ctx.user_id, model.source_message_id == ctx.source_message_id]
    cond += [getattr(model, k) == v for k, v in filters.items()]
    return ctx.session.execute(select(model).where(*cond).limit(1)).scalar_one_or_none()


def _approval(
    ctx: ToolContext,
    action_type: str,
    payload: dict[str, Any],
    summary: str,
    target_key: str,
    target_version: int | None = None,
) -> dict[str, Any]:
    # A newer proposal for the same thing replaces any older pending one.
    for old in ctx.session.execute(
        select(PendingApproval).where(
            PendingApproval.user_id == ctx.user_id,
            PendingApproval.status == "pending",
            PendingApproval.payload["target_key"].astext == target_key,
        )
    ).scalars():
        old.status = "rejected"
        old.result = {"superseded": True}
        old.decided_at = ctx.now
    payload = {**payload, "target_key": target_key}
    approval = PendingApproval(
        user_id=ctx.user_id,
        action_type=action_type,
        payload=payload,
        summary=summary,
        target_version=target_version,
        expires_at=ctx.now + timedelta(hours=24),
    )
    ctx.session.add(approval)
    ctx.session.flush()
    ctx.approvals.append(approval)
    return {
        "status": "proposed_awaiting_user_approval",
        "approval_id": str(approval.id),
        "summary": summary,
        "note": "Nothing has changed yet. The user will see Approve / Reject buttons.",
    }


# --- READ tools ------------------------------------------------------------


class DateArg(BaseModel):
    date: str | None = Field(None, description="YYYY-MM-DD, 'today', 'yesterday' or 'tomorrow'")


@tool("get_day", "read", "Everything recorded and planned for one day: plan blocks, activities, "
      "expenses, tasks, WHOOP health data and the day's review.", DateArg)
def get_day(ctx: ToolContext, a: DateArg):
    return day_view(ctx.session, ctx.user_id, resolve_date(a.date, ctx.settings, ctx.now), ctx.tz)


@tool("get_week", "read", "Weekly totals by day and category, spending, and progress "
      "against weekly targets (measured from records).", DateArg)
def get_week(ctx: ToolContext, a: DateArg):
    return week_view(ctx.session, ctx.user_id, resolve_date(a.date, ctx.settings, ctx.now), ctx.tz)


class SearchArgs(BaseModel):
    query: str = Field(description="Words to look for, e.g. 'python' or 'groceries'")
    kind: Literal["activity", "expense", "task", "note", "memory", "any"] = "any"
    days_back: int = Field(30, ge=1, le=365)


@tool("search_records", "read", "Find earlier records by words, e.g. to resolve 'that Python "
      "session from Tuesday'. Returns ids you can pass to other tools.", SearchArgs)
def search_records(ctx: ToolContext, a: SearchArgs):
    since = ctx.today - timedelta(days=a.days_back)
    like = f"%{a.query}%"
    s, out = ctx.session, {}
    if a.kind in ("activity", "any"):
        rows = s.execute(
            select(ActivitySession)
            .where(
                ActivitySession.user_id == ctx.user_id,
                ActivitySession.deleted_at.is_(None),
                ActivitySession.local_date >= since,
                or_(ActivitySession.title.ilike(like), ActivitySession.category.ilike(like)),
            )
            .order_by(ActivitySession.local_date.desc())
            .limit(15)
        ).scalars()
        out["activities"] = [activity_view(r, ctx.tz) for r in rows]
    if a.kind in ("expense", "any"):
        rows = s.execute(
            select(Expense)
            .where(
                Expense.user_id == ctx.user_id,
                Expense.deleted_at.is_(None),
                Expense.local_date >= since,
                or_(Expense.category.ilike(like), Expense.merchant.ilike(like), Expense.note.ilike(like)),
            )
            .order_by(Expense.local_date.desc())
            .limit(15)
        ).scalars()
        out["expenses"] = [expense_view(r) for r in rows]
    if a.kind in ("task", "any"):
        rows = s.execute(
            select(Task)
            .where(Task.user_id == ctx.user_id, Task.title.ilike(like))
            .order_by(Task.created_at.desc())
            .limit(15)
        ).scalars()
        out["tasks"] = [task_view(r, ctx.tz) for r in rows]
    if a.kind in ("note", "any"):
        rows = s.execute(
            select(InboxItem)
            .where(InboxItem.user_id == ctx.user_id, InboxItem.deleted_at.is_(None),
                   InboxItem.content.ilike(like))
            .order_by(InboxItem.created_at.desc())
            .limit(15)
        ).scalars()
        out["notes"] = [{"id": str(r.id), "kind": r.kind, "content": r.content} for r in rows]
    if a.kind in ("memory", "any"):
        rows = s.execute(
            select(Memory)
            .where(Memory.user_id == ctx.user_id, Memory.deleted_at.is_(None),
                   Memory.content.ilike(like))
            .limit(15)
        ).scalars()
        out["memories"] = [{"id": str(r.id), "content": r.content, "status": r.status} for r in rows]
    return out


class NoArgs(BaseModel):
    pass


@tool("get_goals", "read", "Active goals, milestones, projects and weekly targets with measured "
      "progress this week.", NoArgs)
def get_goals(ctx: ToolContext, a: NoArgs):
    return {"goals": goals_view(ctx.session, ctx.user_id, ctx.today)}


@tool("get_health", "read", "WHOOP data for a day: recovery, HRV, resting HR, sleep, strain, "
      "steps, workouts. Measured source data.", DateArg)
def get_health(ctx: ToolContext, a: DateArg):
    return health_view(ctx.session, ctx.user_id, resolve_date(a.date, ctx.settings, ctx.now))


# --- AUTO tools ----------------------------------------------------------------


class LogActivity(BaseModel):
    title: str = Field(description="Short name, e.g. 'DSA practice', 'Gym - upper day', 'Lunch'")
    category: Category = Field(description="study | work | exercise | commute | meal | rest | "
                               "social | project | meditation | entertainment | chores | other")
    status: ActivityStatus = Field(description="'missed' only if the user said they skipped it")
    date: str | None = Field(None, description="Defaults to today")
    minutes: int | None = Field(None, ge=0, le=24 * 60,
                                description="Only if the user stated a duration")
    start_time: str | None = Field(None, description="HH:MM local, only if stated")
    end_time: str | None = Field(None, description="HH:MM local, only if stated")
    block_id: str | None = Field(None, description="Plan block this fulfils, if any")
    task_id: str | None = None
    goal_id: str | None = None
    notes: str | None = Field(None, description="Only what the user said, e.g. their stated reason")


@tool("log_activity", "auto", "Record something the user did, is doing, or skipped. Never invent a "
      "duration, completion or reason.", LogActivity)
def log_activity(ctx: ToolContext, a: LogActivity):
    day = resolve_date(a.date, ctx.settings, ctx.now)
    started = local_dt(day, a.start_time, ctx.tz, ctx.settings.day_end_hour) if a.start_time else None
    ended = local_dt(day, a.end_time, ctx.tz, ctx.settings.day_end_hour) if a.end_time else None
    minutes = a.minutes
    if started and ended:
        if ended <= started:
            raise ToolError("end_time must be after start_time")
        minutes = int((ended - started).total_seconds() // 60)
    existing = _same_message_duplicate(ctx, ActivitySession, title=a.title, local_date=day)
    if existing:
        return {"status": "already_saved", "activity": activity_view(existing, ctx.tz)}
    block = None
    if a.block_id:
        block = _owned(ctx, TimeBlock, a.block_id, "plan block")
    act = ActivitySession(
        user_id=ctx.user_id,
        title=a.title,
        category=a.category.lower(),
        local_date=day,
        started_at=started,
        ended_at=ended,
        minutes=minutes,
        status=a.status,
        source="chat",
        task_id=_uuid(a.task_id, "task_id"),
        goal_id=_uuid(a.goal_id, "goal_id") or (block.goal_id if block else None),
        block_id=block.id if block else None,
        notes=a.notes,
        source_message_id=ctx.source_message_id,
    )
    ctx.session.add(act)
    ctx.session.flush()
    audit(ctx.session, user_id=ctx.user_id, entity=act, action="create", actor="assistant",
          after=row_dict(act))
    if block is not None and block.status != a.status:
        before = row_dict(block)
        block.status = a.status
        _bump(block, ctx.now)
        audit(ctx.session, user_id=ctx.user_id, entity=block, action="update", actor="assistant",
              before=before, after=row_dict(block))
    dur = f", {minutes} min" if minutes is not None else ""
    ctx.receipts.append(f"Logged {a.title} ({a.status}{dur}, {day.isoformat()})")
    return {"status": "saved", "activity": activity_view(act, ctx.tz)}


class LogExpense(BaseModel):
    amount: float = Field(gt=0, le=100000, description="Amount the user stated. Never guess.")
    category: str = Field(description="food | groceries | transport | health | fitness | shopping | "
                          "bills | entertainment | education | social | other")
    estimated: bool = Field(description="True if the user said 'about', 'around', '~'")
    currency: Literal["GBP", "EUR", "USD", "INR"] = "GBP"
    merchant: str | None = None
    note: str | None = None
    date: str | None = None
    worth_it: int | None = Field(None, ge=1, le=5, description="Only if the user rated it")


@tool("log_expense", "auto", "Record money the user spent. If the amount is missing or unclear, "
      "ask instead of calling this.", LogExpense)
def log_expense(ctx: ToolContext, a: LogExpense):
    day = resolve_date(a.date, ctx.settings, ctx.now)
    pence = round(a.amount * 100)
    existing = _same_message_duplicate(ctx, Expense, amount_pence=pence, local_date=day)
    if existing:
        return {"status": "already_saved", "expense": expense_view(existing)}
    exp = Expense(
        user_id=ctx.user_id,
        amount_pence=pence,
        currency=a.currency,
        category=a.category.lower(),
        merchant=a.merchant,
        note=a.note,
        local_date=day,
        status="estimated" if a.estimated else "confirmed",
        worth_it=a.worth_it,
        source_message_id=ctx.source_message_id,
    )
    ctx.session.add(exp)
    ctx.session.flush()
    audit(ctx.session, user_id=ctx.user_id, entity=exp, action="create", actor="assistant",
          after=row_dict(exp))
    est = " (estimated)" if a.estimated else ""
    ctx.receipts.append(f"Spent {pence_to_str(pence)}{est} on {exp.category}")
    return {"status": "saved", "expense": expense_view(exp)}


class CreateTask(BaseModel):
    title: str
    kind: Literal["task", "reminder", "commitment"] = "task"
    due_date: str | None = Field(None, description="Only if the user gave a day")
    due_time: str | None = Field(None, description="HH:MM, only if the user gave a time")
    important: bool = False
    goal_id: str | None = None


@tool("create_task", "auto", "Create a task or reminder the user clearly stated. Do not invent a "
      "due date; ask if timing matters and is missing.", CreateTask)
def create_task(ctx: ToolContext, a: CreateTask):
    due_date = resolve_date(a.due_date, ctx.settings, ctx.now) if a.due_date else None
    if a.due_time and not due_date:
        due_date = ctx.today
    due_at = local_dt(due_date, a.due_time, ctx.tz, ctx.settings.day_end_hour) if a.due_time else None
    existing = _same_message_duplicate(ctx, Task, title=a.title)
    if existing:
        return {"status": "already_saved", "task": task_view(existing, ctx.tz)}
    task = Task(
        user_id=ctx.user_id,
        title=a.title,
        kind=a.kind,
        due_date=due_date,
        due_at=due_at,
        important=a.important,
        goal_id=_uuid(a.goal_id, "goal_id"),
        source_message_id=ctx.source_message_id,
    )
    ctx.session.add(task)
    ctx.session.flush()
    audit(ctx.session, user_id=ctx.user_id, entity=task, action="create", actor="assistant",
          after=row_dict(task))
    when = f" for {due_date.isoformat()}" if due_date else ""
    ctx.receipts.append(f"Added {a.kind}: {a.title}{when}")
    return {"status": "saved", "task": task_view(task, ctx.tz)}


class InboxArgs(BaseModel):
    kind: Literal["idea", "note"]
    content: str


@tool("create_inbox_item", "auto", "Save an idea or note. Use for 'maybe' plans and thoughts; "
      "never turn an idea into a scheduled task.", InboxArgs)
def create_inbox_item(ctx: ToolContext, a: InboxArgs):
    existing = _same_message_duplicate(ctx, InboxItem, content=a.content)
    if existing:
        return {"status": "already_saved", "id": str(existing.id)}
    item = InboxItem(user_id=ctx.user_id, kind=a.kind, content=a.content,
                     source_message_id=ctx.source_message_id)
    ctx.session.add(item)
    ctx.session.flush()
    audit(ctx.session, user_id=ctx.user_id, entity=item, action="create", actor="assistant",
          after=row_dict(item))
    ctx.receipts.append(f"Saved {a.kind}: {a.content}")
    return {"status": "saved", "id": str(item.id)}


class TaskStatus(BaseModel):
    task_id: str
    status: Literal["done", "cancelled", "open"]
    expected_version: int | None = None


@tool("set_task_status", "auto", "Mark a task done, cancelled or reopened, as the user reported.",
      TaskStatus)
def set_task_status(ctx: ToolContext, a: TaskStatus):
    task = _owned(ctx, Task, a.task_id, "task")
    _check_version(task, a.expected_version)
    before = row_dict(task)
    task.status = a.status
    task.completed_at = ctx.now if a.status == "done" else None
    _bump(task, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=task, action="update", actor="assistant",
          before=before, after=row_dict(task))
    ctx.receipts.append(f"Task '{task.title}' marked {a.status}")
    return {"status": "saved", "task": task_view(task, ctx.tz)}


class BlockStatus(BaseModel):
    block_id: str
    status: ActivityStatus = Field(description="'missed' only if the user said they skipped it")
    expected_version: int | None = None


@tool("set_block_status", "auto", "Update a plan block's status as the user reported (e.g. done, "
      "skipped). For a completed block with a stated duration, use log_activity with block_id.",
      BlockStatus)
def set_block_status(ctx: ToolContext, a: BlockStatus):
    block = _owned(ctx, TimeBlock, a.block_id, "plan block")
    _check_version(block, a.expected_version)
    before = row_dict(block)
    block.status = a.status
    _bump(block, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=block, action="update", actor="assistant",
          before=before, after=row_dict(block))
    ctx.receipts.append(f"Block '{block.title}' marked {a.status}")
    return {"status": "saved", "block_id": str(block.id), "new_status": a.status}


class CorrectActivity(BaseModel):
    activity_id: str
    minutes: int | None = Field(None, ge=0, le=24 * 60)
    status: ActivityStatus | None = None
    title: str | None = None
    date: str | None = None
    notes: str | None = None
    expected_version: int | None = None


@tool("correct_activity", "auto", "Correct an existing activity record (the user said it was "
      "different). Updates in place; never create a duplicate for a correction.", CorrectActivity)
def correct_activity(ctx: ToolContext, a: CorrectActivity):
    act = _owned(ctx, ActivitySession, a.activity_id, "activity")
    _check_version(act, a.expected_version)
    before = row_dict(act)
    changes = []
    if a.minutes is not None:
        act.minutes, act.started_at, act.ended_at = a.minutes, None, None
        changes.append(f"{before['minutes']} -> {a.minutes} min")
    if a.status is not None:
        act.status = a.status
        changes.append(f"status {before['status']} -> {a.status}")
    if a.title is not None:
        act.title = a.title
        changes.append(f"title -> {a.title}")
    if a.date is not None:
        act.local_date = resolve_date(a.date, ctx.settings, ctx.now)
        changes.append(f"date -> {act.local_date.isoformat()}")
    if a.notes is not None:
        act.notes = a.notes
    if not changes and a.notes is None:
        raise ToolError("Nothing to correct")
    _bump(act, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=act, action="update", actor="assistant",
          before=before, after=row_dict(act))
    ctx.receipts.append(f"Corrected {act.title}: " + ", ".join(changes or ["notes"]))
    return {"status": "saved", "activity": activity_view(act, ctx.tz), "before": before}


class CorrectExpense(BaseModel):
    expense_id: str
    amount: float | None = Field(None, gt=0, le=100000)
    category: str | None = None
    merchant: str | None = None
    note: str | None = None
    date: str | None = None
    estimated: bool | None = None
    worth_it: int | None = Field(None, ge=1, le=5)
    expected_version: int | None = None


@tool("correct_expense", "auto", "Correct an existing expense or add a worth-it rating.",
      CorrectExpense)
def correct_expense(ctx: ToolContext, a: CorrectExpense):
    exp = _owned(ctx, Expense, a.expense_id, "expense")
    _check_version(exp, a.expected_version)
    before = row_dict(exp)
    if a.amount is not None:
        exp.amount_pence = round(a.amount * 100)
    if a.category is not None:
        exp.category = a.category.lower()
    if a.merchant is not None:
        exp.merchant = a.merchant
    if a.note is not None:
        exp.note = a.note
    if a.date is not None:
        exp.local_date = resolve_date(a.date, ctx.settings, ctx.now)
    if a.estimated is not None:
        exp.status = "estimated" if a.estimated else "confirmed"
    if a.worth_it is not None:
        exp.worth_it = a.worth_it
    _bump(exp, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=exp, action="update", actor="assistant",
          before=before, after=row_dict(exp))
    ctx.receipts.append(f"Updated expense: {pence_to_str(exp.amount_pence)} {exp.category}")
    return {"status": "saved", "expense": expense_view(exp)}


UNDOABLE = {
    "activity_sessions": ActivitySession,
    "expenses": Expense,
    "tasks": Task,
    "inbox_items": InboxItem,
    "time_blocks": TimeBlock,
    "memories": Memory,
}


@tool("undo_last", "auto", "Undo the most recent change the assistant made (within 24 hours).",
      NoArgs)
def undo_last(ctx: ToolContext, a: NoArgs):
    undone_ids = {
        e.after.get("undid")
        for e in ctx.session.execute(
            select(AuditEvent).where(AuditEvent.user_id == ctx.user_id, AuditEvent.action == "undo")
        ).scalars()
        if e.after
    }
    candidates = ctx.session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.user_id == ctx.user_id,
            AuditEvent.actor == "assistant",
            AuditEvent.action.in_(("create", "update")),
            AuditEvent.entity_type.in_(list(UNDOABLE)),
            AuditEvent.created_at > ctx.now - timedelta(hours=24),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(20)
    ).scalars()
    event = next((e for e in candidates if str(e.id) not in undone_ids), None)
    if event is None:
        raise ToolError("Nothing recent to undo")
    model = UNDOABLE[event.entity_type]
    obj = ctx.session.get(model, uuid.UUID(event.entity_id), with_for_update=True)
    if obj is None:
        raise ToolError("That record no longer exists")
    before = row_dict(obj)
    if event.action == "create":
        if hasattr(obj, "deleted_at"):
            obj.deleted_at = ctx.now
        elif isinstance(obj, Task):
            obj.status = "cancelled"
        else:
            raise ToolError("This change can't be undone automatically")
        what = "removed"
    else:
        restorable = {k: v for k, v in (event.before or {}).items()
                      if k not in ("id", "user_id", "created_at", "updated_at", "version")}
        for key, value in restorable.items():
            col = model.__table__.columns[key]
            setattr(obj, key, _parse_back(col, value))
        what = "restored"
    if hasattr(obj, "version"):
        _bump(obj, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=obj, action="undo", actor="assistant",
          before=before, after={"undid": str(event.id)})
    label = getattr(obj, "title", None) or getattr(obj, "content", None) or event.entity_type
    ctx.receipts.append(f"Undone: {label} {what}")
    return {"status": "undone", "entity": event.entity_type, "id": event.entity_id, "result": what}


def _parse_back(col, value):
    """Turn a JSON value from the audit log back into the column's Python type."""
    from datetime import date as _date
    from datetime import datetime as _datetime

    if value is None:
        return None
    try:
        py = col.type.python_type
    except NotImplementedError:
        return value
    if py is _datetime:
        return _datetime.fromisoformat(value)
    if py is _date:
        return _date.fromisoformat(value)
    if py is uuid.UUID:
        return uuid.UUID(value)
    return value


class SaveMemory(BaseModel):
    content: str = Field(description="One clear sentence, e.g. 'Focuses best before 9am'")
    kind: Literal["preference", "pattern", "fact", "commitment"]
    user_said_explicitly: bool = Field(
        description="True only if the user directly stated it or asked you to remember it. "
        "Your own inference must be False (saved as an unconfirmed observation)."
    )


@tool("save_memory", "auto", "Remember something long-term. Explicit statements are confirmed; "
      "your inferences are saved only as observations to confirm later.", SaveMemory)
def save_memory(ctx: ToolContext, a: SaveMemory):
    status = "confirmed" if a.user_said_explicitly else "observed"
    mem = Memory(
        user_id=ctx.user_id,
        content=a.content,
        kind=a.kind,
        status=status,
        confidence=None if a.user_said_explicitly else 0.5,
        source_message_id=ctx.source_message_id,
        confirmed_at=ctx.now if a.user_said_explicitly else None,
    )
    ctx.session.add(mem)
    ctx.session.flush()
    audit(ctx.session, user_id=ctx.user_id, entity=mem, action="create", actor="assistant",
          after=row_dict(mem))
    ctx.receipts.append(f"Remembered ({status}): {a.content}")
    return {"status": "saved", "memory_id": str(mem.id), "memory_status": status}


class ForgetMemory(BaseModel):
    memory_id: str
    replacement: str | None = Field(None, description="Corrected version, if the user gave one")


@tool("forget_memory", "auto", "Forget or correct a memory because the user asked to.",
      ForgetMemory)
def forget_memory(ctx: ToolContext, a: ForgetMemory):
    mem = _owned(ctx, Memory, a.memory_id, "memory")
    before = row_dict(mem)
    mem.deleted_at = ctx.now
    mem.status = "rejected"
    _bump(mem, ctx.now)
    audit(ctx.session, user_id=ctx.user_id, entity=mem, action="update", actor="user",
          before=before, after=row_dict(mem))
    result: dict[str, Any] = {"status": "forgotten", "memory_id": str(mem.id)}
    if a.replacement:
        new = Memory(user_id=ctx.user_id, content=a.replacement, kind=mem.kind,
                     status="confirmed", confirmed_at=ctx.now,
                     source_message_id=ctx.source_message_id)
        ctx.session.add(new)
        ctx.session.flush()
        audit(ctx.session, user_id=ctx.user_id, entity=new, action="create", actor="user",
              after=row_dict(new))
        result["replacement_id"] = str(new.id)
        ctx.receipts.append(f"Memory corrected: {a.replacement}")
    else:
        ctx.receipts.append(f"Forgot: {mem.content}")
    return result


# --- APPROVAL tools ------------------------------------------------------------


class PlanBlock(BaseModel):
    title: str
    category: str
    start_time: str = Field(description="HH:MM local")
    end_time: str = Field(description="HH:MM local")
    important: bool = False
    checklist: list[str] = Field(default_factory=list, description="Small steps to tick off")
    task_id: str | None = None
    goal_id: str | None = None


class ProposePlan(BaseModel):
    date: str | None = None
    blocks: list[PlanBlock] = Field(min_length=1, max_length=30)
    note: str | None = Field(None, description="Why this plan, in one or two sentences")


def validate_blocks(blocks: list[PlanBlock], day, tz: str, day_end_hour: int) -> list[dict]:
    """Check times and overlaps. Returns normalised blocks (UTC ISO times)."""
    parsed = []
    for b in blocks:
        start = local_dt(day, b.start_time, tz, day_end_hour)
        end = local_dt(day, b.end_time, tz, day_end_hour)
        if end <= start:
            raise ToolError(f"Block '{b.title}': end_time must be after start_time")
        parsed.append((start, end, b))
    parsed.sort(key=lambda x: x[0])
    for (s1, e1, b1), (s2, _e2, b2) in zip(parsed, parsed[1:]):
        if s2 < e1:
            raise ToolError(f"Blocks overlap: '{b1.title}' and '{b2.title}'. Fix the times.")
    return [
        {
            "title": b.title,
            "category": b.category.lower(),
            "starts_at": s.isoformat(),
            "ends_at": e.isoformat(),
            "start_time": b.start_time,
            "end_time": b.end_time,
            "important": b.important,
            "checklist": [{"text": c, "done": False} for c in b.checklist],
            "task_id": b.task_id,
            "goal_id": b.goal_id,
        }
        for s, e, b in parsed
    ]


@tool("propose_plan", "approval", "Propose a (new or revised) time-block plan for a day. Leave "
      "breathing room; don't fill every minute. The user approves before it's used.", ProposePlan)
def propose_plan(ctx: ToolContext, a: ProposePlan):
    day = resolve_date(a.date, ctx.settings, ctx.now)
    blocks = validate_blocks(a.blocks, day, ctx.tz, ctx.settings.day_end_hour)
    scheduled = sum(
        (datetime.fromisoformat(b["ends_at"]) - datetime.fromisoformat(b["starts_at"])).seconds
        for b in blocks
    ) / 3600
    warnings = []
    if scheduled > 14:
        warnings.append(f"{scheduled:.1f} hours scheduled: this is an overloaded day")
    lines = [f"{b['start_time']}-{b['end_time']} {b['title']}" for b in blocks]
    summary = f"Plan for {day.strftime('%a %d %b')}:\n" + "\n".join(lines)
    if a.note:
        summary += f"\n\n{a.note}"
    result = _approval(ctx, "apply_plan", {"date": day.isoformat(), "blocks": blocks, "note": a.note},
                       summary, target_key=f"plan:{day.isoformat()}")
    result["warnings"] = warnings
    return result


class Reschedule(BaseModel):
    target_type: Literal["task", "block"]
    target_id: str
    new_date: str | None = None
    new_start_time: str | None = Field(None, description="HH:MM, for blocks or timed tasks")
    new_end_time: str | None = Field(None, description="HH:MM, blocks only")


@tool("propose_reschedule", "approval", "Move an existing task or plan block to another time or "
      "day. Needs the user's approval.", Reschedule)
def propose_reschedule(ctx: ToolContext, a: Reschedule):
    if a.target_type == "task":
        task = _owned(ctx, Task, a.target_id, "task")
        new_day = resolve_date(a.new_date, ctx.settings, ctx.now) if a.new_date else task.due_date
        summary = f"Move task '{task.title}' to {new_day.isoformat() if new_day else 'no date'}"
        if a.new_start_time:
            summary += f" at {a.new_start_time}"
        return _approval(ctx, "reschedule_task",
                         {"task_id": a.target_id, "new_date": new_day.isoformat() if new_day else None,
                          "new_time": a.new_start_time},
                         summary, target_key=f"task:{a.target_id}", target_version=task.version)
    block = _owned(ctx, TimeBlock, a.target_id, "plan block")
    if not a.new_start_time and not a.new_date:
        raise ToolError("Give a new date and/or new start time")
    summary = f"Move '{block.title}'"
    if a.new_date:
        summary += f" to {resolve_date(a.new_date, ctx.settings, ctx.now).isoformat()}"
    if a.new_start_time:
        summary += f" at {a.new_start_time}" + (f"-{a.new_end_time}" if a.new_end_time else "")
    return _approval(ctx, "reschedule_block",
                     {"block_id": a.target_id,
                      "new_date": resolve_date(a.new_date, ctx.settings, ctx.now).isoformat()
                      if a.new_date else None,
                      "new_start_time": a.new_start_time, "new_end_time": a.new_end_time},
                     summary, target_key=f"block:{a.target_id}", target_version=block.version)


class ProposeGoal(BaseModel):
    kind: Literal["long_term", "milestone", "weekly_target", "project"]
    title: str
    category: str | None = Field(None, description="Activity or spending category it measures")
    metric: Literal["minutes", "sessions", "count", "pence"] | None = None
    target_value: float | None = Field(None, ge=0, description="Only a number the user stated "
                                       "or agreed to. Spending limits in pence.")
    period: Literal["day", "week", "month"] | None = None
    deadline: str | None = None
    parent_id: str | None = None


@tool("propose_goal", "approval", "Propose a new goal, milestone, project or weekly target. Never "
      "invent target numbers; use what the user stated or ask.", ProposeGoal)
def propose_goal(ctx: ToolContext, a: ProposeGoal):
    target = ""
    if a.target_value is not None and a.metric:
        target = (f": {pence_to_str(int(a.target_value))}" if a.metric == "pence"
                  else f": {a.target_value:g} {a.metric}") + (f" per {a.period}" if a.period else "")
    summary = f"New {a.kind.replace('_', ' ')} '{a.title}'{target}"
    return _approval(ctx, "create_goal", a.model_dump(), summary,
                     target_key=f"goal:new:{a.title.lower()}")


class GoalChange(BaseModel):
    goal_id: str
    title: str | None = None
    target_value: float | None = Field(None, ge=0)
    metric: Literal["minutes", "sessions", "count", "pence"] | None = None
    period: Literal["day", "week", "month"] | None = None
    deadline: str | None = None
    status: Literal["active", "done", "archived"] | None = None
    reason: str = Field(description="Why, with evidence from records")


@tool("propose_goal_change", "approval", "Propose changing an approved goal or target. Never "
      "applied without the user's approval.", GoalChange)
def propose_goal_change(ctx: ToolContext, a: GoalChange):
    goal = _owned(ctx, Goal, a.goal_id, "goal")
    changes = {k: v for k, v in a.model_dump().items()
               if k not in ("goal_id", "reason") and v is not None}
    if not changes:
        raise ToolError("No changes given")
    summary = f"Change '{goal.title}': " + ", ".join(f"{k} -> {v}" for k, v in changes.items())
    summary += f"\nReason: {a.reason}"
    return _approval(ctx, "update_goal", {"goal_id": a.goal_id, "changes": changes}, summary,
                     target_key=f"goal:{a.goal_id}", target_version=goal.version)


class ProposeDelete(BaseModel):
    entity_type: Literal["activity", "expense", "task", "note", "goal"]
    id: str


DELETABLE = {"activity": ActivitySession, "expense": Expense, "task": Task, "note": InboxItem,
             "goal": Goal}


@tool("propose_delete", "approval", "Propose deleting a record. Needs approval.", ProposeDelete)
def propose_delete(ctx: ToolContext, a: ProposeDelete):
    obj = _owned(ctx, DELETABLE[a.entity_type], a.id, a.entity_type)
    label = getattr(obj, "title", None) or getattr(obj, "content", None) or (
        pence_to_str(obj.amount_pence) if isinstance(obj, Expense) else a.id)
    return _approval(ctx, "delete_record", {"entity_type": a.entity_type, "id": a.id},
                     f"Delete {a.entity_type}: {label}", target_key=f"{a.entity_type}:{a.id}",
                     target_version=getattr(obj, "version", None))


class ConfirmMemory(BaseModel):
    memory_id: str


@tool("propose_confirm_memory", "approval", "Ask the user to confirm an observed pattern so it "
      "becomes an established preference.", ConfirmMemory)
def propose_confirm_memory(ctx: ToolContext, a: ConfirmMemory):
    mem = _owned(ctx, Memory, a.memory_id, "memory")
    if mem.status != "observed":
        raise ToolError("Only observed memories need confirmation")
    return _approval(ctx, "confirm_memory", {"memory_id": a.memory_id},
                     f"Remember as true: {mem.content}", target_key=f"memory:{a.memory_id}",
                     target_version=mem.version)


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

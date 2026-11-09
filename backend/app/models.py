"""Database tables (batch 1 subset).

Later batches add goals, tasks, plans, activity sessions, expenses, memories
and approvals. Every table here matches migration 0001; a test checks that the
models and the migration never drift apart.
"""
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Numeric,
    UniqueConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    Time,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def _ts_now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts_now()


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(Text, server_default="Europe/London", nullable=False)
    day_end_hour: Mapped[int] = mapped_column(SmallInteger, server_default="4", nullable=False)
    quiet_start: Mapped[time] = mapped_column(Time, server_default="22:30", nullable=False)
    quiet_end: Mapped[time] = mapped_column(Time, server_default="07:00", nullable=False)
    max_proactive_per_day: Mapped[int] = mapped_column(
        SmallInteger, server_default="5", nullable=False
    )
    monthly_ai_budget_pence: Mapped[int] = mapped_column(
        Integer, server_default="1000", nullable=False
    )
    morning_brief_time: Mapped[time] = mapped_column(Time, server_default="07:30", nullable=False)
    evening_review_time: Mapped[time] = mapped_column(Time, server_default="21:30", nullable=False)
    proactive_cooldown_minutes: Mapped[int] = mapped_column(
        SmallInteger, server_default="60", nullable=False
    )
    # gentle | firm | adaptive (firm on good days, gentle on tough ones)
    coaching_style: Mapped[str] = mapped_column(Text, server_default="adaptive", nullable=False)
    updated_at: Mapped[datetime] = _ts_now()


class TelegramLink(Base):
    """Which Telegram account belongs to which user. Only linked accounts are served."""

    __tablename__ = "telegram_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    linked_at: Mapped[datetime] = _ts_now()


class LinkCode(Base):
    """One-time code you send to the bot as `/start <code>` to link Telegram."""

    __tablename__ = "link_codes"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()


class InboundUpdate(Base):
    """Every raw Telegram update, stored before anything else happens.

    `update_id` is unique, so if Telegram delivers the same update twice the
    second insert does nothing (duplicate protection).
    """

    __tablename__ = "inbound_updates"

    id: Mapped[uuid.UUID] = _uuid_pk()
    update_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="received", nullable=False)
    received_at: Mapped[datetime] = _ts_now()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Message(Base):
    """Conversation log: what you said and what the assistant said."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)  # user | assistant | system
    kind: Mapped[str] = mapped_column(Text, server_default="text", nullable=False)  # text | voice | other
    content: Mapped[str] = mapped_column(Text, server_default="", nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    inbound_update_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inbound_updates.id", ondelete="SET NULL")
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    created_at: Mapped[datetime] = _ts_now()


class Job(Base):
    """Durable background work. Survives restarts; claimed with SKIP LOCKED."""

    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "run_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5", nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class OutboundMessage(Base):
    """Everything the bot sent. Used later for cooldowns, caps and 'already asked'."""

    __tablename__ = "outbound_messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # reply | proactive | system
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # sent | failed
    created_at: Mapped[datetime] = _ts_now()


class AuditEvent(Base):
    """Before/after record of every important change. Powers undo and history."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(Text, nullable=False)  # user | assistant | system
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts_now()


# ---------------------------------------------------------------------------
# Batch 3+ tables
# ---------------------------------------------------------------------------


def _user_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


def _version() -> Mapped[int]:
    """Optimistic locking: every update bumps it; stale writers are rejected."""
    return mapped_column(Integer, server_default="1", nullable=False)


class Goal(Base):
    """Goals, milestones, weekly targets and projects in one tree."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "kind in ('long_term','milestone','weekly_target','project')", name="goal_kind"
        ),
        CheckConstraint("status in ('active','done','archived')", name="goal_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(Text)  # study, gym, work, ...
    metric: Mapped[str | None] = mapped_column(Text)  # minutes | sessions | count | pence
    target_value: Mapped[float | None] = mapped_column(Numeric)
    period: Mapped[str | None] = mapped_column(Text)  # day | week | month
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    version: Mapped[int] = _version()
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("kind in ('task','reminder','commitment')", name="task_kind"),
        CheckConstraint("status in ('open','done','cancelled')", name="task_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, server_default="task", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="open", nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    important: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = _version()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class InboxItem(Base):
    """Ideas and notes. An idea never becomes a scheduled task without the user."""

    __tablename__ = "inbox_items"
    __table_args__ = (CheckConstraint("kind in ('idea','note')", name="inbox_kind"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()


ACTIVITY_STATUSES = ("planned", "in_progress", "completed", "missed", "cancelled", "unknown")


class ActivitySession(Base):
    """What actually happened. Duration is only stored if the user stated it or
    it came from a timer."""

    __tablename__ = "activity_sessions"
    __table_args__ = (
        CheckConstraint(
            "status in ('planned','in_progress','completed','missed','cancelled','unknown')",
            name="activity_status",
        ),
        CheckConstraint("minutes is null or minutes >= 0", name="activity_minutes"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, server_default="chat", nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    block_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = _version()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_pence > 0", name="expense_amount_positive"),
        CheckConstraint("status in ('confirmed','estimated')", name="expense_status"),
        CheckConstraint("worth_it is null or worth_it between 1 and 5", name="expense_worth_it"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    amount_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, server_default="GBP", nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    merchant: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(Text, server_default="confirmed", nullable=False)
    worth_it: Mapped[int | None] = mapped_column(SmallInteger)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = _version()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class Memory(Base):
    """Long-term memory. 'observed' is a guess; only 'confirmed' is treated as true."""

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint("status in ('observed','confirmed','rejected')", name="memory_status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    content: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, server_default="fact", nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric)
    source_message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = _version()
    created_at: Mapped[datetime] = _ts_now()


class PendingApproval(Base):
    """An exact proposed action that only runs after the user approves it, once."""

    __tablename__ = "pending_approvals"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','executing','executed','rejected','expired','stale','failed')",
            name="approval_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="pending", nullable=False)
    target_version: Mapped[int | None] = mapped_column(Integer)
    result: Mapped[dict | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()


class DailyPlan(Base):
    __tablename__ = "daily_plans"
    __table_args__ = (UniqueConstraint("user_id", "local_date", name="uq_plan_day"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _ts_now()


class PlanVersion(Base):
    """Every revision of a day's plan is kept; the original is never overwritten."""

    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("plan_id", "version_no", name="uq_plan_version"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("daily_plans.id", ondelete="CASCADE"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()


class TimeBlock(Base):
    __tablename__ = "time_blocks"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="block_order"),
        CheckConstraint(
            "status in ('planned','in_progress','completed','missed','cancelled','unknown')",
            name="block_status",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    plan_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("plan_versions.id", ondelete="CASCADE"), nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    important: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    status: Mapped[str] = mapped_column(Text, server_default="planned", nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    checklist: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"), nullable=False)
    version: Mapped[int] = _version()


class DailyReview(Base):
    __tablename__ = "daily_reviews"
    __table_args__ = (UniqueConstraint("user_id", "local_date", name="uq_review_day"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[datetime] = _ts_now()


class FocusSession(Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (
        CheckConstraint(
            "status in ('running','paused','completed','stopped')", name="focus_status"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tasks.id", ondelete="SET NULL")
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="SET NULL")
    )
    work_minutes: Mapped[int] = mapped_column(SmallInteger, server_default="25", nullable=False)
    break_minutes: Mapped[int] = mapped_column(SmallInteger, server_default="5", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_seconds: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default="running", nullable=False)
    # Unique: a finished session produces exactly one activity record.
    activity_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("activity_sessions.id", ondelete="SET NULL"), unique=True
    )


class IntegrationConnection(Base):
    """OAuth connections (WHOOP). Tokens are stored encrypted."""

    __tablename__ = "integration_connections"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_user_provider"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active", nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts_now()
    updated_at: Mapped[datetime] = _ts_now()


class OAuthState(Base):
    """One-time 'state' values that protect the OAuth flow from forgery (CSRF)."""

    __tablename__ = "oauth_states"

    state: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WhoopDay(Base):
    """One WHOOP physiological cycle (wake to next wake): source data, not AI opinion."""

    __tablename__ = "whoop_days"
    __table_args__ = (UniqueConstraint("user_id", "cycle_id", name="uq_whoop_cycle"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    cycle_id: Mapped[str] = mapped_column(Text, nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    cycle_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cycle_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strain: Mapped[float | None] = mapped_column(Numeric)
    kilojoule: Mapped[float | None] = mapped_column(Numeric)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer)
    max_heart_rate: Mapped[int | None] = mapped_column(Integer)
    step_count: Mapped[int | None] = mapped_column(Integer)
    recovery_score: Mapped[float | None] = mapped_column(Numeric)
    hrv_rmssd_milli: Mapped[float | None] = mapped_column(Numeric)
    resting_heart_rate: Mapped[float | None] = mapped_column(Numeric)
    sleep_performance: Mapped[float | None] = mapped_column(Numeric)
    sleep_hours: Mapped[float | None] = mapped_column(Numeric)
    raw: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)
    updated_at: Mapped[datetime] = _ts_now()


class WhoopWorkout(Base):
    __tablename__ = "whoop_workouts"
    __table_args__ = (UniqueConstraint("user_id", "workout_id", name="uq_whoop_workout"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    workout_id: Mapped[str] = mapped_column(Text, nullable=False)
    local_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    sport_name: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strain: Mapped[float | None] = mapped_column(Numeric)
    average_heart_rate: Mapped[int | None] = mapped_column(Integer)
    max_heart_rate: Mapped[int | None] = mapped_column(Integer)
    kilojoule: Mapped[float | None] = mapped_column(Numeric)
    raw: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"), nullable=False)


class LLMUsage(Base):
    """Token usage per model call, for the monthly cost cap."""

    __tablename__ = "llm_usage"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _ts_now()


class WebSession(Base):
    """Web app login sessions. Only a hash of the token is stored."""

    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = _user_fk()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = _ts_now()


class LoginCode(Base):
    """6-digit web login codes, sent to the user's Telegram. Hash stored only."""

    __tablename__ = "login_codes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = _user_fk()
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default="0", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

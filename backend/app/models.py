"""Database tables (batch 1 subset).

Later batches add goals, tasks, plans, activity sessions, expenses, memories
and approvals. Every table here matches migration 0001; a test checks that the
models and the migration never drift apart.
"""
import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
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

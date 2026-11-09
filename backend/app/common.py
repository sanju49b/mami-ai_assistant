"""Shared helpers: dates in the user's timezone, audit records, serialising rows."""
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from app.models import AuditEvent, UserSettings
from app.timeutil import local_date


class ToolError(ValueError):
    """A problem the model (or the user) can fix, e.g. a missing or invalid field."""


class Conflict(ToolError):
    """The record changed since it was read (optimistic locking)."""


def get_settings_row(session: Session, user_id: uuid.UUID) -> UserSettings:
    row = session.get(UserSettings, user_id)
    if row is None:
        row = UserSettings(user_id=user_id)
        session.add(row)
        session.flush()
        session.refresh(row)
    return row


def today_for(settings: UserSettings, now: datetime) -> date:
    return local_date(now, settings.timezone, settings.day_end_hour)


def resolve_date(value: str | None, settings: UserSettings, now: datetime) -> date:
    """Accept 'today' / 'yesterday' / 'tomorrow' / 'YYYY-MM-DD'."""
    today = today_for(settings, now)
    if value is None or value == "" or value == "today":
        return today
    if value == "yesterday":
        return today - timedelta(days=1)
    if value == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"Invalid date {value!r}; use YYYY-MM-DD, today, yesterday or tomorrow") from exc


def local_dt(day: date, hhmm: str, tz: str, day_end_hour: int = 4) -> datetime:
    """Convert a local date + 'HH:MM' into an aware UTC datetime.

    A time earlier than the day-end hour (e.g. 01:00) is treated as after
    midnight, i.e. the next calendar day, because the user's day runs to 04:00.
    """
    try:
        t = time.fromisoformat(hhmm)
    except ValueError as exc:
        raise ToolError(f"Invalid time {hhmm!r}; use HH:MM") from exc
    if t.hour < day_end_hour:
        day = day + timedelta(days=1)
    return datetime.combine(day, t, tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


def to_local_hhmm(moment: datetime | None, tz: str) -> str | None:
    if moment is None:
        return None
    return moment.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    return value


def row_dict(obj: Any, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    mapper = sa_inspect(obj).mapper
    return {
        c.key: jsonable(getattr(obj, c.key))
        for c in mapper.column_attrs
        if c.key not in exclude
    }


def audit(
    session: Session,
    *,
    user_id: uuid.UUID,
    entity: Any = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    action: str,
    actor: str,
    before: dict | None = None,
    after: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        user_id=user_id,
        entity_type=entity_type or (entity.__tablename__ if entity is not None else "unknown"),
        entity_id=entity_id or (str(entity.id) if entity is not None else None),
        action=action,
        actor=actor,
        before=before,
        after=after,
    )
    session.add(event)
    return event


def pence_to_str(pence: int) -> str:
    return f"£{pence / 100:,.2f}"

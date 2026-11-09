"""Time helpers.

Rules (from the architecture doc):
- Store every timestamp in UTC (Postgres `timestamptz`).
- Each record also gets a `local_date`: the user's calendar day in their
  timezone, where the day ends at `day_end_hour` (default 04:00), so a log
  sent at 01:30 still counts towards the previous day.
- Use `zoneinfo`, which knows the UK clock changes (BST <-> GMT).
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/London"
DEFAULT_DAY_END_HOUR = 4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def local_date(
    moment: datetime, tz: str = DEFAULT_TZ, day_end_hour: int = DEFAULT_DAY_END_HOUR
) -> date:
    """Return the user's 'day' for a UTC moment."""
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    local = moment.astimezone(ZoneInfo(tz))
    if local.hour < day_end_hour:
        return local.date() - timedelta(days=1)
    return local.date()

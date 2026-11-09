from datetime import date, datetime, timezone

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app.db import get_engine
from app.models import Base
from app.timeutil import local_date


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_models_match_migrations():
    """The ORM models and the migrated schema must never drift apart."""
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


@pytest.mark.parametrize(
    ("utc", "expected"),
    [
        # Summer (BST = UTC+1): 02:59 UTC is 03:59 local, before the 04:00 day end.
        (datetime(2026, 7, 1, 2, 59, tzinfo=timezone.utc), date(2026, 6, 30)),
        (datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc), date(2026, 7, 1)),
        # Clocks go back on 25 Oct 2026: 03:30 UTC is 03:30 GMT, still the 24th.
        (datetime(2026, 10, 25, 3, 30, tzinfo=timezone.utc), date(2026, 10, 24)),
        (datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc), date(2026, 10, 25)),
        # Clocks go forward on 28 Mar 2027: 03:30 UTC is 04:30 BST, a new day.
        (datetime(2027, 3, 28, 3, 30, tzinfo=timezone.utc), date(2027, 3, 28)),
        (datetime(2027, 3, 28, 2, 30, tzinfo=timezone.utc), date(2027, 3, 27)),
        # Winter (GMT): midday is simply that day.
        (datetime(2026, 12, 1, 12, 0, tzinfo=timezone.utc), date(2026, 12, 1)),
    ],
)
def test_local_date_handles_day_end_and_clock_changes(utc, expected):
    assert local_date(utc) == expected


def test_local_date_rejects_naive_datetimes():
    with pytest.raises(ValueError):
        local_date(datetime(2026, 1, 1, 12, 0))

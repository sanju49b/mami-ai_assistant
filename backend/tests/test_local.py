"""Local mode: Telegram long polling instead of a webhook, plus startup checks."""
import threading

import httpx
import pytest
from sqlalchemy import func, select

from app import local
from app.cli import create_user, make_link_code
from app.config import Settings
from app.db import session_scope
from app.models import InboundUpdate, Job, Message, TelegramLink, User
from tests.helpers import text_update


def count(model) -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(model)).scalar_one()


def test_supabase_url_is_accepted_as_copied():
    s = Settings(database_url="postgresql://postgres.abc:pw@aws-0-eu-west-2.pooler.supabase.com:5432/postgres")
    assert s.database_url.startswith("postgresql+psycopg://postgres.abc:pw@")
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    already = "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url=already).database_url == already


def test_placeholder_password_is_caught(monkeypatch):
    monkeypatch.setattr(local, "get_settings", lambda: Settings(
        database_url="postgresql://postgres:[YOUR-PASSWORD]@h:5432/postgres",
        telegram_bot_token="t", anthropic_api_key="k"))
    with pytest.raises(SystemExit):
        local.check_settings()


def test_missing_keys_are_caught(monkeypatch, capsys):
    monkeypatch.setattr(local, "get_settings", lambda: Settings(
        database_url="postgresql://u:p@h/db", telegram_bot_token="", anthropic_api_key=""))
    with pytest.raises(SystemExit):
        local.check_settings()
    out = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN" in out and "ANTHROPIC_API_KEY" in out


def test_polled_updates_are_stored_once_and_offset_advances():
    user_id = create_user("Sri")
    code = make_link_code(user_id)
    batch = [text_update(f"/start {code}", update_id=500), text_update("gym done 6-7:30", update_id=501)]

    assert local.process_updates(batch) == 502
    assert count(TelegramLink) == 1
    with session_scope() as s:
        texts = s.execute(select(Message.content)).scalars().all()
    assert "gym done 6-7:30" in texts
    jobs_after_first = count(Job)

    # Telegram can resend a batch (e.g. after a crash): nothing is duplicated.
    assert local.process_updates(batch) == 502
    assert count(InboundUpdate) == 2
    assert count(Job) == jobs_after_first


class FakeBot:
    """getUpdates: fails once (network drop), then returns a batch, then stops."""

    def __init__(self, batch, stop):
        self.batch, self.stop, self.calls, self.offsets = batch, stop, 0, []

    def get_updates(self, offset, timeout=25):
        self.calls += 1
        self.offsets.append(offset)
        if self.calls == 1:
            raise httpx.ConnectError("offline")
        if self.calls == 2:
            return self.batch
        self.stop.set()
        return []


def test_polling_survives_network_errors_and_moves_offset(monkeypatch):
    user_id = create_user("Sri")
    code = make_link_code(user_id)
    stop = threading.Event()
    bot = FakeBot([text_update(f"/start {code}", update_id=900)], stop)
    monkeypatch.setattr(local, "run_due_jobs", lambda: 0)
    monkeypatch.setattr(stop, "wait", lambda seconds=None: stop.is_set())  # no real sleeping

    local.poll_telegram(bot, stop)

    assert bot.offsets == [None, None, 901]
    assert count(TelegramLink) == 1


def test_first_run_creates_owner_and_prints_link(capsys):
    class Bot:
        def get_me(self):
            return {"username": "mami_sri_bot"}

    local.ensure_owner_and_link(Bot())
    out = capsys.readouterr().out
    assert "https://t.me/mami_sri_bot?start=" in out
    assert count(User) == 1

    # Running again doesn't create a second profile.
    local.ensure_owner_and_link(Bot())
    assert count(User) == 1


def test_days_missed_while_laptop_was_off_are_still_closed():
    from datetime import timedelta

    from app.jobs import run_due_jobs
    from app.models import DailyReview
    from app.scheduler import ensure_jobs
    from app.timeutil import utc_now
    from tests.conftest_helpers import make_linked_user

    make_linked_user()
    with session_scope() as s:
        link = s.execute(select(TelegramLink)).scalar_one()
        link.linked_at = utc_now() - timedelta(days=3, hours=1)
    with session_scope() as s:
        ensure_jobs(s, utc_now())
    run_due_jobs()
    with session_scope() as s:
        closed = s.execute(select(DailyReview.local_date)).scalars().all()
    assert len(closed) >= 3  # the three missed days, each saved even with no messages
    with session_scope() as s:
        assert ensure_jobs(s, utc_now()) == 0  # and never twice

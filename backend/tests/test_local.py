"""Local mode: chat in the web app on this laptop, no Telegram, no login."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import local
from app.config import Settings, get_settings
from app.db import session_scope
from app.main import create_app
from app.models import DailyReview, Message, PendingApproval, TelegramLink, User
from app.telegram.client import set_telegram_client
from app.timeutil import utc_now
from tests.conftest_helpers import call, tools_then_text


def count(model, *where) -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


@pytest.fixture
def local_client(monkeypatch):
    monkeypatch.setattr(get_settings(), "local_mode", True)
    set_telegram_client(None)  # local mode: messages are only saved for the web chat
    local.ensure_owner()
    with TestClient(create_app(), base_url="http://testserver") as c:
        yield c
    set_telegram_client(None)


# --- settings and startup ---------------------------------------------------


def test_supabase_url_is_accepted_as_copied():
    s = Settings(database_url="postgresql://postgres.abc:pw@aws-0-eu-west-1.pooler.supabase.com:5432/postgres")
    assert s.database_url.startswith("postgresql+psycopg://postgres.abc:pw@")
    assert Settings(database_url="postgres://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"
    already = "postgresql+psycopg://u:p@h/db"
    assert Settings(database_url=already).database_url == already


def test_only_database_and_claude_key_are_needed(monkeypatch, capsys):
    monkeypatch.setattr(local, "get_settings", lambda: Settings(
        database_url="postgresql://u:p@h/db", anthropic_api_key="k", telegram_bot_token=""))
    local.check_settings()  # no Telegram token needed

    monkeypatch.setattr(local, "get_settings", lambda: Settings(
        database_url="postgresql://postgres:[YOUR-PASSWORD]@h:5432/postgres", anthropic_api_key=""))
    with pytest.raises(SystemExit):
        local.check_settings()
    out = capsys.readouterr().out
    assert "[YOUR-PASSWORD]" in out and "ANTHROPIC_API_KEY" in out


def test_owner_is_created_once():
    local.ensure_owner()
    local.ensure_owner()
    assert count(User) == 1
    assert count(TelegramLink) == 1  # the scheduler's "where to reach you" row


# --- chat -------------------------------------------------------------------


def test_no_login_needed_locally(local_client):
    me = local_client.get("/api/me").json()
    assert me["name"] == "Sri" and me["local"] is True


def test_chat_message_is_saved_and_answered(local_client, llm):
    llm.responses.extend(tools_then_text(
        call("log_expense", amount=4, category="food", estimated=False, merchant="coffee"),
        text="Logged £4 coffee."))
    data = local_client.post("/api/chat", json={"text": "£4 coffee"}).json()
    roles = [(m["role"], m["text"]) for m in data["messages"]]
    assert roles[0] == ("user", "£4 coffee")
    assert roles[1][0] == "assistant" and roles[1][1].startswith("Logged £4 coffee.")
    # Shown again after a reload, in order.
    again = local_client.get("/api/chat").json()["messages"]
    assert [m["role"] for m in again] == ["user", "assistant"]


def test_message_is_kept_when_claude_fails(local_client, llm):
    class Broken:
        def create(self, **_):
            raise RuntimeError("network down")

    from app.llm import set_llm

    set_llm(Broken())
    r = local_client.post("/api/chat", json={"text": "gym done 6-7:30"})
    assert r.status_code == 502 and "saved" in r.json()["detail"]
    assert count(Message, Message.role == "user", Message.content == "gym done 6-7:30") == 1


def test_proposals_show_as_cards_not_extra_messages(local_client, llm):
    llm.responses.extend(tools_then_text(
        call("propose_goal", title="Solve 50 DSA problems", kind="long_term",
             metric="count", target_value=50),
        text="Here's a goal for you to approve."))
    data = local_client.post("/api/chat", json={"text": "set a goal: 50 DSA problems"}).json()
    assert len(data["approvals"]) == 1
    assert not any(m["text"].startswith("Proposal:") for m in data["messages"])
    a = data["approvals"][0]
    assert local_client.post(f"/api/approvals/{a['id']}", json={"decision": "approve"}).status_code == 200
    assert local_client.get("/api/chat").json()["approvals"] == []
    assert count(PendingApproval, PendingApproval.status == "executed") == 1


def test_days_missed_while_laptop_was_off_are_still_closed():
    from app.jobs import run_due_jobs
    from app.scheduler import ensure_jobs

    local.ensure_owner()
    with session_scope() as s:
        link = s.execute(select(TelegramLink)).scalar_one()
        link.linked_at = utc_now() - timedelta(days=3, hours=1)
    with session_scope() as s:
        ensure_jobs(s, utc_now())
    run_due_jobs()
    assert count(DailyReview) >= 3  # the three missed days, saved even with no messages
    with session_scope() as s:
        assert ensure_jobs(s, utc_now()) == 0  # and never twice

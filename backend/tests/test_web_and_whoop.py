import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.crypto import decrypt
from app.db import session_scope
from app.integrations import whoop
from app.jobs import run_due_jobs
from app.models import ActivitySession, IntegrationConnection, Job, LoginCode, WhoopDay
from app.scheduler import ensure_jobs
from app.timeutil import utc_now
from tests.conftest_helpers import make_linked_user


@pytest.fixture
def uid():
    return make_linked_user()


def login(client, telegram) -> None:
    assert client.post("/api/auth/request-code").status_code == 200
    run_due_jobs()
    code = telegram.sent[-1][1].split("code: ")[1][:6]
    assert client.post("/api/auth/verify", json={"code": code}).status_code == 200


def test_web_login_with_telegram_code(client, telegram, uid):
    assert client.get("/api/day").status_code == 401
    login(client, telegram)
    day = client.get("/api/day").json()
    assert "blocks" in day and "approvals" in day
    with session_scope() as s:
        job = s.execute(select(Job).where(Job.kind == "login_code")).scalar_one()
        assert job.payload == {"sent": True}  # code not kept in the jobs table


def test_wrong_code_is_rejected_and_attempts_are_limited(client, telegram, uid):
    client.post("/api/auth/request-code")
    for _ in range(5):
        assert client.post("/api/auth/verify", json={"code": "000000"}).status_code in (400, 429)
    with session_scope() as s:
        assert s.execute(select(LoginCode)).scalar_one().attempts >= 5
    run_due_jobs()
    code = telegram.sent[-1][1].split("code: ")[1][:6]
    assert client.post("/api/auth/verify", json={"code": code}).status_code == 429


def test_focus_session_is_recorded_exactly_once(client, telegram, uid):
    login(client, telegram)
    f = client.post("/api/focus", json={"title": "DSA", "category": "study"}).json()
    with session_scope() as s:  # pretend it started 30 minutes ago
        from app.models import FocusSession

        s.get(FocusSession, uuid.UUID(f["id"])).started_at = utc_now() - timedelta(minutes=30)
    client.post(f"/api/focus/{f['id']}", json={"action": "complete"})
    client.post(f"/api/focus/{f['id']}", json={"action": "complete"})  # double tap
    with session_scope() as s:
        acts = s.execute(select(ActivitySession)).scalars().all()
        assert len(acts) == 1 and acts[0].minutes == 30 and acts[0].source == "focus"


def test_web_edit_with_old_version_gets_409(client, telegram, uid):
    from app.models import Task

    login(client, telegram)
    with session_scope() as s:
        t = Task(user_id=uid, title="x")
        s.add(t)
        s.flush()
        tid = str(t.id)
    assert client.patch(f"/api/tasks/{tid}", json={"version": 1, "status": "done"}).status_code == 200
    assert client.patch(f"/api/tasks/{tid}", json={"version": 1, "status": "open"}).status_code == 409


def test_scheduler_is_idempotent(uid):
    with session_scope() as s:
        first = ensure_jobs(s, utc_now())
    with session_scope() as s:
        second = ensure_jobs(s, utc_now())
    assert first > 0 and second == 0


# --- WHOOP ---------------------------------------------------------------------

def fake_whoop(requests_seen: list):
    now = utc_now()

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            form = parse_qs(request.content.decode())
            n = len([r for r in requests_seen if r.url.path.endswith("/token")])
            return httpx.Response(200, json={"access_token": f"access-{n}", "expires_in": 3600,
                                             "refresh_token": f"refresh-{n}",
                                             "scope": "offline read:recovery",
                                             "token_type": "bearer", "_grant": form["grant_type"]})
        assert request.headers["Authorization"].startswith("Bearer access-")
        start = (now - timedelta(hours=5)).isoformat().replace("+00:00", "Z")
        if path.endswith("/cycle"):
            return httpx.Response(200, json={"records": [{
                "id": 93845, "start": start, "end": None, "step_count": 8421,
                "score": {"strain": 9.2, "kilojoule": 8000, "average_heart_rate": 70,
                          "max_heart_rate": 150}}], "next_token": None})
        if path.endswith("/recovery"):
            return httpx.Response(200, json={"records": [{
                "cycle_id": 93845, "sleep_id": "s1",
                "score": {"recovery_score": 44, "hrv_rmssd_milli": 41.5,
                          "resting_heart_rate": 58}}]})
        if path.endswith("/activity/sleep"):
            return httpx.Response(200, json={"records": [{
                "id": "s1", "cycle_id": 93845, "nap": False,
                "score": {"sleep_performance_percentage": 78, "stage_summary": {
                    "total_light_sleep_time_milli": 3 * 3600_000,
                    "total_slow_wave_sleep_time_milli": 1.5 * 3600_000,
                    "total_rem_sleep_time_milli": 1.5 * 3600_000}}}]})
        if path.endswith("/activity/workout"):
            return httpx.Response(200, json={"records": [{
                "id": "w-uuid", "sport_name": "weightlifting", "start": start, "end": start,
                "score": {"strain": 11.0, "average_heart_rate": 120, "max_heart_rate": 165}}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_whoop_connect_sync_and_token_rotation(client, telegram, uid):
    seen: list = []
    whoop.transport = fake_whoop(seen)
    try:
        # /whoop in Telegram -> link with a one-time state
        upd = {"update_id": 777, "message": {"message_id": 1, "date": int(utc_now().timestamp()),
               "chat": {"id": 111, "type": "private"}, "from": {"id": 111, "is_bot": False,
               "first_name": "S"}, "text": "/whoop"}}
        from tests.helpers import SECRET_HEADERS

        client.post("/telegram/webhook", json=upd, headers=SECRET_HEADERS)
        run_due_jobs()
        url = [t for _, t in telegram.sent if "whoop.com" in t][0].split("\n")[-1]
        q = parse_qs(urlparse(url).query)
        assert q["scope"][0].startswith("offline") and q["redirect_uri"][0] == \
            "https://dm.example.com/whoop/callback"
        state = q["state"][0]

        assert client.get("/whoop/callback", params={"state": "forged", "code": "c"}).status_code == 400
        r = client.get("/whoop/callback", params={"state": state, "code": "auth-code"})
        assert r.status_code == 200 and "connected" in r.text
        # The same state can't be used twice.
        assert client.get("/whoop/callback", params={"state": state, "code": "x"}).status_code == 400

        run_due_jobs()  # initial sync + announcement
        with session_scope() as s:
            day = s.execute(select(WhoopDay)).scalar_one()
            assert float(day.recovery_score) == 44 and day.step_count == 8421
            assert float(day.sleep_hours) == 6.0
            conn = s.execute(select(IntegrationConnection)).scalar_one()
            assert conn.access_token_enc != "access-1"  # stored encrypted
            assert decrypt(conn.refresh_token_enc) == "refresh-1"
            conn.expires_at = utc_now()  # force a refresh on next call
        with session_scope() as s:
            whoop.sync(s, uuid.UUID(str(uid)))
        with session_scope() as s:
            conn = s.execute(select(IntegrationConnection)).scalar_one()
            assert decrypt(conn.refresh_token_enc) == "refresh-2"  # rotated token saved
            assert s.execute(select(WhoopDay)).scalars().all().__len__() == 1  # upsert, no dup
        assert any("WHOOP connected" in t for _, t in telegram.sent)
    finally:
        whoop.transport = None


# --- free hosting helpers ---------------------------------------------------------

def test_cron_tick_requires_the_key_and_runs_jobs(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "cron_key", "tick-secret")
    assert client.get("/cron/tick").status_code == 404
    assert client.get("/cron/tick", params={"key": "wrong"}).status_code == 404
    assert client.get("/cron/tick", params={"key": "tick-secret"}).json() == {"status": "ok"}


def test_setup_page_creates_user_sets_webhook_and_links_once(client, monkeypatch):
    from app.models import User
    from app.telegram import client as tg

    calls = []

    class FakeBot:
        def __init__(self, token):
            pass

        def set_webhook(self, url, secret):
            calls.append((url, secret))

        def get_me(self):
            return {"username": "sri_mami_bot"}

        def get_webhook_info(self):
            return {"pending_update_count": 2, "last_error_message": "Wrong response from the webhook: 401"}

    monkeypatch.setattr("app.admin.HttpTelegramClient", FakeBot)
    assert client.get("/setup", params={"key": "nope"}).status_code == 404
    page = client.get("/setup", params={"key": "test-secret"}).text
    assert "https://t.me/sri_mami_bot?start=" in page
    assert "Telegram linked: <b>not yet</b>" in page and "401" in page
    assert calls[0][0] == "https://dm.example.com/telegram/webhook"
    client.get("/setup", params={"key": "test-secret"})  # opening again is safe
    with session_scope() as s:
        assert len(s.execute(select(User)).scalars().all()) == 1

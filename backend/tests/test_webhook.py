from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.cli import create_user, make_link_code
from app.db import session_scope
from app.jobs import run_due_jobs
from app.models import InboundUpdate, Job, LinkCode, Message, OutboundMessage, TelegramLink
from tests.helpers import (
    MY_TELEGRAM_ID,
    SECRET_HEADERS,
    STRANGER_ID,
    text_update,
    voice_update,
)


def count(model) -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(model)).scalar_one()


def post(client, update, headers=SECRET_HEADERS):
    return client.post("/telegram/webhook", json=update, headers=headers)


def link_me(client) -> str:
    user_id = create_user("Sri")
    code = make_link_code(user_id)
    assert post(client, text_update(f"/start {code}")).json()["status"] == "linked"
    return user_id


# --- Security ------------------------------------------------------------


def test_missing_or_wrong_secret_is_rejected(client):
    assert post(client, text_update("hi"), headers={}).status_code == 401
    wrong = {"X-Telegram-Bot-Api-Secret-Token": "nope"}
    assert post(client, text_update("hi"), headers=wrong).status_code == 401
    assert count(InboundUpdate) == 0


def test_unconfigured_secret_returns_503(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "telegram_webhook_secret", "")
    assert post(client, text_update("hi")).status_code == 503


def test_malformed_update_is_rejected(client):
    assert post(client, {"message": {}}).status_code == 400


def test_stranger_is_stored_raw_but_ignored(client, telegram):
    create_user("Sri")
    response = post(client, text_update("hello bot", sender=STRANGER_ID))
    assert response.json()["status"] == "ignored_unlinked"
    assert count(InboundUpdate) == 1
    assert count(Message) == 0
    run_due_jobs()
    assert telegram.sent == []


# --- Linking -------------------------------------------------------------


def test_link_with_one_time_code(client, telegram):
    user_id = link_me(client)
    run_due_jobs()
    assert len(telegram.sent) == 1
    assert telegram.sent[0][0] == MY_TELEGRAM_ID
    with session_scope() as s:
        link = s.execute(select(TelegramLink)).scalar_one()
        assert str(link.user_id) == user_id


def test_link_code_cannot_be_reused_by_someone_else(client):
    user_id = create_user("Sri")
    code = make_link_code(user_id)
    assert post(client, text_update(f"/start {code}")).json()["status"] == "linked"
    stolen = post(client, text_update(f"/start {code}", sender=STRANGER_ID))
    assert stolen.json()["status"] == "link_failed"
    assert count(TelegramLink) == 1


def test_expired_link_code_fails(client):
    user_id = create_user("Sri")
    code = make_link_code(user_id)
    with session_scope() as s:
        s.get(LinkCode, code).expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert post(client, text_update(f"/start {code}")).json()["status"] == "link_failed"
    assert count(TelegramLink) == 0


# --- Messages ------------------------------------------------------------


def test_text_message_is_saved_and_acknowledged(client, telegram):
    link_me(client)
    run_due_jobs()
    telegram.sent.clear()

    response = post(client, text_update("45 min DSA and spent £12 on lunch"))
    assert response.json()["status"] == "stored"
    with session_scope() as s:
        msg = s.execute(select(Message).where(Message.role == "user")).scalar_one()
        assert msg.content == "45 min DSA and spent £12 on lunch"
        assert msg.kind == "text"

    assert run_due_jobs()["done"] == 1
    assert telegram.sent == [(MY_TELEGRAM_ID, "Saved.")]


def test_duplicate_webhook_delivery_is_processed_once(client, telegram):
    """Spec scenario 7: Telegram delivers the same update several times."""
    link_me(client)
    run_due_jobs()
    telegram.sent.clear()

    update = text_update("finished Python assignment")
    statuses = [post(client, update).json()["status"] for _ in range(3)]
    assert statuses == ["stored", "duplicate", "duplicate"]
    run_due_jobs()
    run_due_jobs()

    with session_scope() as s:
        user_msgs = s.execute(
            select(func.count()).select_from(Message).where(Message.role == "user")
        ).scalar_one()
        jobs = s.execute(
            select(func.count()).select_from(Job).where(Job.kind == "process_message")
        ).scalar_one()
    assert user_msgs == 1
    assert jobs == 1
    assert len(telegram.sent) == 1


def test_voice_note_is_saved_with_file_reference(client, telegram):
    link_me(client)
    run_due_jobs()
    telegram.sent.clear()

    assert post(client, voice_update()).json()["status"] == "stored"
    with session_scope() as s:
        msg = s.execute(select(Message).where(Message.role == "user")).scalar_one()
        assert msg.kind == "voice"
        assert msg.meta["file_id"] == "VOICE123"
    run_due_jobs()
    assert "dictate" in telegram.sent[0][1]


def test_late_night_message_counts_towards_previous_day(client):
    link_me(client)
    # 01:30 GMT on 2 Dec is before the 04:00 day end, so it belongs to 1 Dec.
    sent_at = datetime(2026, 12, 2, 1, 30, tzinfo=timezone.utc)
    post(client, text_update("wind-down done", sent_at=sent_at))
    with session_scope() as s:
        msg = s.execute(select(Message).where(Message.role == "user")).scalar_one()
        assert msg.local_date == date(2026, 12, 1)


def test_reply_is_not_sent_twice_when_job_reruns(client, telegram, db):
    """If a job is retried after its reply was sent, the reply isn't sent again."""
    link_me(client)
    run_due_jobs()
    telegram.sent.clear()
    post(client, text_update("hello"))
    run_due_jobs()
    assert len(telegram.sent) == 1

    with session_scope() as s:
        job = s.execute(select(Job).where(Job.kind == "process_message")).scalar_one()
        job.status = "pending"  # simulate a retry of an already-completed job
        job.run_at = datetime.now(timezone.utc)
    run_due_jobs()
    assert len(telegram.sent) == 1
    assert count(OutboundMessage) == 2  # link confirmation + one ack

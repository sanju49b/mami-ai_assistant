import threading
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import session_scope
from app.jobs import HANDLERS, backoff, enqueue, run_due_jobs
from app.models import Job


def get_job(key: str) -> Job:
    with session_scope() as s:
        return s.execute(select(Job).where(Job.dedupe_key == key)).scalar_one()


def make_due(key: str) -> None:
    with session_scope() as s:
        s.execute(select(Job).where(Job.dedupe_key == key)).scalar_one().run_at = datetime.now(
            timezone.utc
        )


def test_enqueue_is_deduplicated():
    with session_scope() as s:
        assert enqueue(s, "noop", "same-key") is True
        assert enqueue(s, "noop", "same-key") is False


def test_successful_job_runs_once():
    calls = []
    HANDLERS["count_me"] = lambda session, job: calls.append(job.id)
    with session_scope() as s:
        enqueue(s, "count_me", "k1")
    assert run_due_jobs() == {"done": 1, "retry": 0, "failed": 0}
    assert run_due_jobs() == {"done": 0, "retry": 0, "failed": 0}
    assert len(calls) == 1


def test_future_job_waits_until_due():
    HANDLERS["later"] = lambda session, job: None
    with session_scope() as s:
        enqueue(s, "later", "k2", run_at=datetime.now(timezone.utc) + timedelta(hours=1))
    assert run_due_jobs()["done"] == 0
    assert get_job("k2").status == "pending"


def test_failing_job_retries_with_backoff_then_fails():
    """Spec scenario 11: a scheduled job fails and retries."""

    def boom(session, job):
        raise RuntimeError("Telegram is down")

    HANDLERS["boom"] = boom
    with session_scope() as s:
        enqueue(s, "boom", "k3")
        s.flush()
        s.execute(select(Job).where(Job.dedupe_key == "k3")).scalar_one().max_attempts = 3

    assert run_due_jobs()["retry"] == 1
    job = get_job("k3")
    assert job.status == "pending" and job.attempts == 1
    assert "Telegram is down" in job.last_error
    assert job.run_at > datetime.now(timezone.utc)  # pushed into the future

    make_due("k3")
    assert run_due_jobs()["retry"] == 1
    make_due("k3")
    assert run_due_jobs()["failed"] == 1
    assert get_job("k3").status == "failed"


def test_failed_attempt_rolls_back_its_writes():
    """A handler that writes then fails must leave nothing behind."""

    def half_done(session, job):
        enqueue(session, "noop", "should-not-exist")
        raise RuntimeError("fail after write")

    HANDLERS["half_done"] = half_done
    with session_scope() as s:
        enqueue(s, "half_done", "k4")
    run_due_jobs()
    with session_scope() as s:
        assert s.execute(select(Job).where(Job.dedupe_key == "should-not-exist")).first() is None


def test_backoff_grows_and_is_capped():
    assert backoff(1) == timedelta(seconds=30)
    assert backoff(2) == timedelta(seconds=60)
    assert backoff(20) == timedelta(hours=1)


def test_abandoned_running_job_is_picked_up_again():
    HANDLERS["recover"] = lambda session, job: None
    with session_scope() as s:
        enqueue(s, "recover", "k5")
        s.flush()
        job = s.execute(select(Job).where(Job.dedupe_key == "k5")).scalar_one()
        job.status = "running"
        job.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert run_due_jobs()["done"] == 1


def test_two_workers_never_run_the_same_job():
    calls = []
    lock = threading.Lock()

    def slow(session, job):
        with lock:
            calls.append(job.dedupe_key)

    HANDLERS["slow"] = slow
    with session_scope() as s:
        for i in range(20):
            enqueue(s, "slow", f"p{i}")

    threads = [threading.Thread(target=run_due_jobs) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(calls) == sorted(f"p{i}" for i in range(20))

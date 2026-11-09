"""Durable background jobs stored in Postgres.

How it works:
- `enqueue()` inserts a job with a unique `dedupe_key`. Enqueuing the same key
  twice does nothing, so a job can never be created twice.
- `run_due_jobs()` claims due jobs with `FOR UPDATE SKIP LOCKED`, so two
  workers never take the same job, then runs each in its own transaction.
- A failing job is retried with exponential backoff until `max_attempts`,
  then marked `failed` (it stays in the table for inspection).
"""
import logging
import traceback
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db import session_scope
from app.models import Job
from app.timeutil import utc_now

log = logging.getLogger(__name__)

Handler = Callable[[Session, Job], None]
HANDLERS: dict[str, Handler] = {}

LOCK_SECONDS = 300


def handler(kind: str) -> Callable[[Handler], Handler]:
    """Register a function as the handler for a job kind."""

    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn

    return register


def enqueue(
    session: Session,
    kind: str,
    dedupe_key: str,
    payload: dict | None = None,
    user_id: uuid.UUID | None = None,
    run_at: datetime | None = None,
) -> bool:
    """Add a job. Returns False if a job with this dedupe_key already exists."""
    stmt = (
        insert(Job)
        .values(
            kind=kind,
            dedupe_key=dedupe_key,
            payload=payload or {},
            user_id=user_id,
            run_at=run_at or utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
        .returning(Job.id)
    )
    return session.execute(stmt).scalar_one_or_none() is not None


def backoff(attempts: int) -> timedelta:
    """30s, 60s, 120s, 240s ... capped at 1 hour."""
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


def _claim_one() -> uuid.UUID | None:
    """Claim the oldest due job, marking it running with a lock expiry."""
    now = utc_now()
    with session_scope() as session:
        job = session.execute(
            select(Job)
            .where(
                ((Job.status == "pending") & (Job.run_at <= now))
                # A 'running' job whose lock expired was abandoned (crash): take it over.
                | ((Job.status == "running") & (Job.locked_until < now))
            )
            .order_by(Job.run_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_until = now + timedelta(seconds=LOCK_SECONDS)
        job.updated_at = now
        return job.id


def _run(job_id: uuid.UUID) -> str:
    try:
        with session_scope() as session:
            job = session.get(Job, job_id)
            fn = HANDLERS.get(job.kind)
            if fn is None:
                raise RuntimeError(f"no handler for job kind {job.kind!r}")
            fn(session, job)
            job.status = "done"
            job.locked_until = None
            job.last_error = None
            job.updated_at = utc_now()
        return "done"
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded
        log.warning("job %s failed: %s", job_id, exc)
        error = "".join(traceback.format_exception_only(exc)).strip()[:2000]
        with session_scope() as session:
            job = session.get(Job, job_id)
            final = job.attempts >= job.max_attempts
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status="failed" if final else "pending",
                    run_at=utc_now() + backoff(job.attempts),
                    locked_until=None,
                    last_error=error,
                    updated_at=utc_now(),
                )
            )
        return "failed" if final else "retry"


def run_due_jobs(limit: int = 50) -> dict[str, int]:
    """Run up to `limit` due jobs. Returns counts by outcome."""
    counts = {"done": 0, "retry": 0, "failed": 0}
    for _ in range(limit):
        job_id = _claim_one()
        if job_id is None:
            break
        counts[_run(job_id)] += 1
    return counts

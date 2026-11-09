"""Job handlers: what each background job does.

Every handler runs inside its own transaction (see jobs.py). If it raises, all
of its writes are rolled back and the job is retried.
"""
import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import approvals, reviews
from app.agent.runner import AgentResult, run_agent
from app.common import get_settings_row
from app.integrations import whoop
from app.jobs import handler
from app.models import Job, Message, PendingApproval, Task
from app.notify import chat_id_for, require_gate, send_proactive
from app.telegram.client import get_telegram_client
from app.telegram.outbound import send_text
from app.timeutil import utc_now


def approval_buttons(a: PendingApproval):
    return [[("✅ Approve", f"ap:{a.id}:a"), ("✖ Reject", f"ap:{a.id}:r")]]


def lock_user(session: Session, user_id: uuid.UUID) -> None:
    """Process one user's messages strictly one at a time (held until commit)."""
    session.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": str(user_id)})


def deliver(session: Session, user_id: uuid.UUID, chat_id: int, result: AgentResult, key: str,
            kind: str = "reply") -> None:
    send_text(session, user_id=user_id, chat_id=chat_id, text=result.reply, kind=kind,
              dedupe_key=key)
    for a in result.approvals:
        send_text(session, user_id=user_id, chat_id=chat_id,
                  text=f"Proposal:\n{a.summary}\n\nApprove, reject, or reply with changes.",
                  kind="system", dedupe_key=f"approval:{a.id}", buttons=approval_buttons(a))


@handler("send_text")
def send_text_job(session: Session, job: Job) -> None:
    send_text(session, user_id=job.user_id, chat_id=job.payload["chat_id"],
              text=job.payload["text"], kind=job.payload.get("kind", "system"),
              dedupe_key=f"job:{job.dedupe_key}")


@handler("process_message")
def process_message(session: Session, job: Job) -> None:
    message = session.get(Message, uuid.UUID(job.payload["message_id"]))
    if message is None:
        return
    lock_user(session, message.user_id)
    chat_id = job.payload["chat_id"]
    if message.kind == "voice":
        # Claude reads text and images, not audio; Claude is the only AI we use.
        send_text(session, user_id=message.user_id, chat_id=chat_id, kind="reply",
                  dedupe_key=f"ack:{message.id}",
                  text=("I can't listen to voice notes. Tap the microphone on your keyboard to "
                        "dictate instead, then send it as text."))
        return
    if message.kind != "text" or not message.content.strip():
        send_text(session, user_id=message.user_id, chat_id=chat_id, kind="reply",
                  dedupe_key=f"ack:{message.id}",
                  text="Saved. I can only read text messages for now.")
        return
    result = run_agent(session, message.user_id, message.content, utc_now(),
                       source_message_id=message.id)
    deliver(session, message.user_id, chat_id, result, key=f"ack:{message.id}")


@handler("decide_approval")
def decide_approval(session: Session, job: Job) -> None:
    p = job.payload
    lock_user(session, job.user_id)
    outcome = approvals.decide(session, job.user_id, uuid.UUID(p["approval_id"]),
                               "approve" if p["decision"] == "a" else "reject", utc_now())
    try:
        get_telegram_client().answer_callback(p["callback_query_id"], outcome["status"])
    except Exception:  # noqa: BLE001 - the button spinner is cosmetic
        pass
    send_text(session, user_id=job.user_id, chat_id=p["chat_id"], text=outcome["message"],
              kind="system", dedupe_key=f"decision:{p['callback_query_id']}")


@handler("whoop_link")
def whoop_link(session: Session, job: Job) -> None:
    chat_id = job.payload["chat_id"]
    if not whoop.is_configured():
        msg = ("WHOOP isn't set up on the server yet (needs WHOOP_CLIENT_ID, WHOOP_CLIENT_SECRET "
               "and APP_ENCRYPTION_KEY).")
    else:
        url = whoop.start_connect(session, job.user_id)
        msg = f"Tap to connect WHOOP to Mami (link works once, for 15 minutes):\n{url}"
    send_text(session, user_id=job.user_id, chat_id=chat_id, text=msg, kind="system",
              dedupe_key=f"job:{job.dedupe_key}")


@handler("whoop_sync")
def whoop_sync(session: Session, job: Job) -> None:
    counts = whoop.sync(session, job.user_id, days=int(job.payload.get("days", 3)))
    if job.payload.get("announce"):
        chat_id = chat_id_for(session, job.user_id)
        if chat_id:
            send_text(session, user_id=job.user_id, chat_id=chat_id, kind="system",
                      dedupe_key=f"job:{job.dedupe_key}",
                      text=(f"WHOOP connected. Synced {counts.get('cycles', 0)} days and "
                            f"{counts.get('workouts', 0)} workouts. I'll check your recovery "
                            "each morning."))


@handler("morning_brief")
def morning_brief(session: Session, job: Job) -> None:
    uid = job.user_id
    lock_user(session, uid)
    day = date.fromisoformat(job.payload["date"])
    # A brief is pointless after midday.
    stale = datetime.combine(day, datetime.min.time()).replace(tzinfo=utc_now().tzinfo) + \
        timedelta(hours=12)
    if not require_gate(session, uid, utc_now(), stale_after=stale):
        return
    try:
        whoop.sync(session, uid, days=2)  # freshest recovery for planning
    except Exception:  # noqa: BLE001 - planning works without WHOOP
        pass
    result = run_agent(session, uid, None, utc_now(), trigger="morning_brief")
    if result.budget_blocked:
        return
    chat_id = chat_id_for(session, uid)
    if chat_id:
        deliver(session, uid, chat_id, result, key=f"job:{job.dedupe_key}", kind="proactive")


@handler("evening_review")
def evening_review(session: Session, job: Job) -> None:
    uid = job.user_id
    day = date.fromisoformat(job.payload["date"])
    text_body = reviews.evening_message(session, uid, day)
    if text_body is None:
        return  # nothing important unreported: stay quiet
    if not require_gate(session, uid, utc_now(), stale_after=utc_now() + timedelta(hours=3)):
        return
    send_proactive(session, uid, text_body, dedupe_key=f"job:{job.dedupe_key}")


@handler("day_close")
def day_close(session: Session, job: Job) -> None:
    lock_user(session, job.user_id)
    reviews.close_day(session, job.user_id, date.fromisoformat(job.payload["date"]), utc_now())


@handler("weekly_review")
def weekly_review(session: Session, job: Job) -> None:
    uid = job.user_id
    lock_user(session, uid)
    if not require_gate(session, uid, utc_now(), stale_after=utc_now() + timedelta(hours=14)):
        return
    result = run_agent(session, uid, None, utc_now(), trigger="weekly_review")
    if result.budget_blocked:
        return
    chat_id = chat_id_for(session, uid)
    if chat_id:
        deliver(session, uid, chat_id, result, key=f"job:{job.dedupe_key}", kind="proactive")


@handler("reminder")
def reminder(session: Session, job: Job) -> None:
    task = session.get(Task, uuid.UUID(job.payload["task_id"]))
    if task is None or task.status != "open":
        return
    if not require_gate(session, task.user_id, utc_now(), important=True):
        return
    when = ""
    if task.due_at:
        s = get_settings_row(session, task.user_id)
        from app.common import to_local_hhmm

        when = f" ({to_local_hhmm(task.due_at, s.timezone)})"
    send_proactive(session, task.user_id, f"Reminder: {task.title}{when}",
                   dedupe_key=f"job:{job.dedupe_key}")


@handler("login_code")
def login_code(session: Session, job: Job) -> None:
    chat_id = chat_id_for(session, job.user_id)
    if chat_id:
        send_text(session, user_id=job.user_id, chat_id=chat_id, kind="system",
                  dedupe_key=f"job:{job.dedupe_key}",
                  text=(f"Your web login code: {job.payload['code']}\nIt expires in 10 minutes. "
                        "If you didn't ask for it, ignore this."))
    job.payload = {"sent": True}  # don't keep the code lying around in the jobs table

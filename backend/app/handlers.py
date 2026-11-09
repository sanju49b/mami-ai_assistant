"""Job handlers.

Batch 1 behaviour: confirm that each message was saved. In batch 3 the
`process_message` handler hands the message to the AI agent instead.
"""
import uuid

from sqlalchemy.orm import Session

from app.jobs import handler
from app.models import Job, Message
from app.telegram.outbound import send_text


@handler("send_text")
def send_text_job(session: Session, job: Job) -> None:
    send_text(
        session,
        user_id=job.user_id,
        chat_id=job.payload["chat_id"],
        text=job.payload["text"],
        kind=job.payload.get("kind", "system"),
        dedupe_key=f"job:{job.dedupe_key}",
    )


@handler("process_message")
def process_message(session: Session, job: Job) -> None:
    message = session.get(Message, uuid.UUID(job.payload["message_id"]))
    if message is None:
        return
    if message.kind == "voice":
        reply = "Voice note saved. Transcription is switched on in a later build step."
    elif message.kind == "text":
        reply = "Saved."
    else:
        reply = "Saved. I can only read text and voice notes for now."
    send_text(
        session,
        user_id=message.user_id,
        chat_id=job.payload["chat_id"],
        text=reply,
        kind="reply",
        dedupe_key=f"ack:{message.id}",
    )

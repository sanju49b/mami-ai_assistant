"""Builders for fake Telegram updates."""
import itertools
from datetime import datetime, timezone

SECRET_HEADERS = {"X-Telegram-Bot-Api-Secret-Token": "test-secret"}
_ids = itertools.count(1000)

MY_TELEGRAM_ID = 111
STRANGER_ID = 999


def text_update(
    text: str,
    sender: int = MY_TELEGRAM_ID,
    update_id: int | None = None,
    sent_at: datetime | None = None,
) -> dict:
    uid = update_id if update_id is not None else next(_ids)
    when = sent_at or datetime.now(timezone.utc)
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "date": int(when.timestamp()),
            "chat": {"id": sender, "type": "private"},
            "from": {"id": sender, "is_bot": False, "first_name": "Test"},
            "text": text,
        },
    }


def voice_update(sender: int = MY_TELEGRAM_ID) -> dict:
    uid = next(_ids)
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {"id": sender, "type": "private"},
            "from": {"id": sender, "is_bot": False, "first_name": "Test"},
            "voice": {"file_id": "VOICE123", "duration": 7, "mime_type": "audio/ogg"},
        },
    }

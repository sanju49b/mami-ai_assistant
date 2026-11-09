"""Helpers shared by the scenario tests."""
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.agent.runner import run_agent
from app.cli import create_user
from app.db import session_scope
from app.llm import LLMResponse, text_block, tool_call
from app.models import TelegramLink
from app.timeutil import utc_now

CHAT = 111


def make_linked_user():
    import uuid

    uid = uuid.UUID(create_user("Sri"))
    with session_scope() as s:
        s.add(TelegramLink(user_id=uid, telegram_user_id=CHAT, chat_id=CHAT))
    return uid


def tools_then_text(*calls: dict, text: str = "Done.") -> list[LLMResponse]:
    """Script: one model turn calling these tools, then a final text reply."""
    blocks = [tool_call(c["name"], c["input"], id=f"tu_{i}") for i, c in enumerate(calls)]
    return [LLMResponse(content=blocks, stop_reason="tool_use"),
            LLMResponse(content=[text_block(text)], stop_reason="end_turn")]


def call(name: str, **input) -> dict:
    return {"name": name, "input": input}


def agent(llm, user_id, text, *calls, reply="Done.", now: datetime | None = None,
          source_message_id=None):
    llm.responses.extend(tools_then_text(*calls, text=reply))
    with session_scope() as s:
        return run_agent(s, user_id, text, now or utc_now(), source_message_id=source_message_id)


def count(model, *where) -> int:
    with session_scope() as s:
        return s.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def yesterday_iso() -> str:
    from app.common import get_settings_row, today_for

    with session_scope() as s:
        from app.models import User

        uid = s.execute(select(User.id)).scalars().first()
        return (today_for(get_settings_row(s, uid), utc_now()) - timedelta(days=1)).isoformat()

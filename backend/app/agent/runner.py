"""The agent loop: ask Claude, run the tools it asks for, repeat, then reply.

Honesty guarantee: the final reply ends with a receipt built from the tools
that actually succeeded, not from the model's words.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.context import build_context, history_messages
from app.agent.prompts import SYSTEM_PROMPT, TRIGGER_INSTRUCTIONS
from app.agent.tools import ToolContext, execute, tool_definitions
from app.common import ToolError, get_settings_row
from app.config import get_settings
from app.llm import LLMResponse, get_llm
from app.models import LLMUsage, PendingApproval

log = logging.getLogger(__name__)

MAX_STEPS = 8


@dataclass
class AgentResult:
    reply: str
    approvals: list[PendingApproval] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    budget_blocked: bool = False


def month_spend_pence(session: Session, user_id: uuid.UUID, now: datetime) -> int:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    micros = session.execute(
        select(func.coalesce(func.sum(LLMUsage.cost_usd_micros), 0)).where(
            LLMUsage.user_id == user_id, LLMUsage.created_at >= start
        )
    ).scalar_one()
    return int(int(micros) / 1_000_000 * get_settings().usd_to_gbp * 100)


def record_usage(session: Session, user_id, model: str, purpose: str, resp: LLMResponse) -> None:
    u = resp.usage
    session.add(
        LLMUsage(
            user_id=user_id,
            model=model,
            purpose=purpose,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_write_tokens=u.cache_write_tokens,
            cost_usd_micros=u.cost_usd_micros(model),
        )
    )


def _display_name(session: Session, user_id: uuid.UUID) -> str:
    from app.models import User

    user = session.get(User, user_id)
    return user.display_name if user else "the user"


def run_agent(
    session: Session,
    user_id: uuid.UUID,
    user_text: str | None,
    now: datetime,
    *,
    source_message_id: uuid.UUID | None = None,
    trigger: str = "message",
) -> AgentResult:
    settings = get_settings_row(session, user_id)
    budget = settings.monthly_ai_budget_pence
    if month_spend_pence(session, user_id, now) >= budget:
        return AgentResult(
            reply=(f"Your AI budget for this month (£{budget / 100:.2f}) is used up, so I can't "
                   "think about this right now. Your message is saved. You can raise the cap in "
                   "Settings."),
            budget_blocked=True,
        )

    ctx = ToolContext(session=session, user_id=user_id, settings=settings, now=now,
                      source_message_id=source_message_id)
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT.replace("{coaching_style}", settings.coaching_style)
            .replace("{name}", _display_name(session, user_id)),
            "cache_control": {"type": "ephemeral"},
        }
    ]
    messages = history_messages(session, user_id, exclude_id=source_message_id)
    current = [{"type": "text", "text": build_context(session, user_id, settings, now)}]
    instruction = TRIGGER_INSTRUCTIONS.get(trigger, "")
    if instruction:
        current.append({"type": "text", "text": instruction})
    if user_text:
        current.append({"type": "text", "text": user_text})
    if messages and messages[-1]["role"] == "user":
        messages[-1] = {"role": "user", "content": [{"type": "text", "text": messages[-1]["content"]}]
                        + current}
    else:
        messages.append({"role": "user", "content": current})

    tools = tool_definitions()
    model = get_settings().agent_model
    llm = get_llm()
    final_text = ""
    for _step in range(MAX_STEPS):
        resp = llm.create(model=model, system=system, messages=messages, tools=tools, max_tokens=2048)
        record_usage(session, user_id, model, trigger, resp)
        messages.append({"role": "assistant", "content": resp.content})
        if not resp.tool_uses:
            final_text = resp.text
            break
        results = []
        for call in resp.tool_uses:
            try:
                output = execute(ctx, call["name"], call.get("input") or {})
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": json.dumps(output, default=str)})
            except ToolError as exc:
                results.append({"type": "tool_result", "tool_use_id": call["id"],
                                "content": f"Error: {exc}", "is_error": True})
        messages.append({"role": "user", "content": results})
    else:
        final_text = final_text or "I got stuck partway. Here's what I managed to save."

    reply = final_text.strip()
    if ctx.receipts:
        reply += "\n\n" + "\n".join(f"✓ {r}" for r in ctx.receipts)
        reply += "\n(Reply 'undo' to reverse the last one.)"
    if ctx.approvals:
        reply += "\n\nWaiting for your approval below."
    return AgentResult(reply=reply or "OK.", approvals=ctx.approvals, receipts=ctx.receipts)

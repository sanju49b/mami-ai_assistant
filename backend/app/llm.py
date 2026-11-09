"""The single doorway to the AI model.

Everything else calls `get_llm().create(...)`. The real implementation talks to
the Claude API; tests swap in `ScriptedLLM`, which returns pre-written replies.
That keeps tests free, fast and deterministic.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings

# USD per million tokens (input, output, cache read, cache write 5m).
# Source: platform.claude.com/docs/en/about-claude/pricing (Nov 2026).
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost_usd_micros(self, model: str) -> int:
        p_in, p_out, p_read, p_write = PRICES.get(model, PRICES["claude-sonnet-5-5"])
        usd = (
            self.input_tokens * p_in
            + self.output_tokens * p_out
            + self.cache_read_tokens * p_read
            + self.cache_write_tokens * p_write
        ) / 1_000_000
        return int(usd * 1_000_000)


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]  # Claude content blocks as plain dicts
    stop_reason: str
    usage: Usage = field(default_factory=Usage)

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLM(Protocol):
    def create(
        self,
        *,
        model: str,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...


class ClaudeLLM:
    def __init__(self, api_key: str) -> None:
        import anthropic

        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=60)

    def create(self, *, model, system, messages, tools=None, max_tokens=2048) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        response = self._client.messages.create(**kwargs)
        u = response.usage
        return LLMResponse(
            content=[block.model_dump(exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ),
        )


@dataclass
class ScriptedLLM:
    """Test double: returns queued responses in order and records every call."""

    responses: list[LLMResponse] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, *, model, system, messages, tools=None, max_tokens=2048) -> LLMResponse:
        import copy

        self.calls.append({"model": model, "system": system,
                           "messages": copy.deepcopy(messages), "tools": tools})
        if not self.responses:
            return LLMResponse(content=[{"type": "text", "text": "OK."}], stop_reason="end_turn")
        return self.responses.pop(0)


def tool_call(name: str, input: dict, id: str | None = None) -> dict:
    """Helper for tests: a tool_use content block."""
    return {"type": "tool_use", "id": id or f"tu_{name}", "name": name, "input": input}


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


_llm: LLM | None = None


def get_llm() -> LLM:
    global _llm
    if _llm is None:
        _llm = ClaudeLLM(get_settings().anthropic_api_key)
    return _llm


def set_llm(llm: LLM | None) -> None:
    global _llm
    _llm = llm

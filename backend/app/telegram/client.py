"""Thin wrapper around the Telegram Bot API.

The rest of the app talks to `TelegramClient`, never to HTTP directly, so tests
can swap in `FakeTelegramClient` and nothing real is sent.
"""
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import get_settings

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


class TelegramClient(Protocol):
    def send_message(self, chat_id: int, text: str) -> int:
        """Send a text message; return Telegram's message_id."""
        ...


class HttpTelegramClient:
    def __init__(self, token: str, timeout: float = 10.0) -> None:
        if not token:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        self._base = f"{API_BASE}/bot{token}"
        self._timeout = timeout

    def _call(self, method: str, payload: dict) -> dict:
        response = httpx.post(f"{self._base}/{method}", json=payload, timeout=self._timeout)
        data = response.json()
        if not data.get("ok"):
            # Never include the token (it is in the URL) in error messages.
            raise TelegramError(f"{method} failed: {data.get('description', response.status_code)}")
        return data["result"]

    def send_message(self, chat_id: int, text: str) -> int:
        result = self._call("sendMessage", {"chat_id": chat_id, "text": text})
        return int(result["message_id"])

    def set_webhook(self, url: str, secret_token: str) -> None:
        self._call(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "callback_query"],
            },
        )


@dataclass
class FakeTelegramClient:
    """Records messages instead of sending them. Used in tests."""

    sent: list[tuple[int, str]] = field(default_factory=list)
    fail: bool = False

    def send_message(self, chat_id: int, text: str) -> int:
        if self.fail:
            raise TelegramError("simulated failure")
        self.sent.append((chat_id, text))
        return len(self.sent)


_client: TelegramClient | None = None


def get_telegram_client() -> TelegramClient:
    global _client
    if _client is None:
        _client = HttpTelegramClient(get_settings().telegram_bot_token)
    return _client


def set_telegram_client(client: TelegramClient | None) -> None:
    """Override the client (tests use this)."""
    global _client
    _client = client

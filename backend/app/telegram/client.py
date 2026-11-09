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


Buttons = list[list[tuple[str, str]]]  # rows of (label, callback_data)


class TelegramClient(Protocol):
    def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> int:
        """Send a text message (optionally with inline buttons); return its message_id."""
        ...

    def answer_callback(self, callback_query_id: str, text: str = "") -> None: ...


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

    def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> int:
        payload: dict = {"chat_id": chat_id, "text": text[:4096]}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row] for row in buttons
                ]
            }
        result = self._call("sendMessage", payload)
        return int(result["message_id"])

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text})

    def get_me(self) -> dict:
        return self._call("getMe", {})

    def delete_webhook(self) -> None:
        """Polling (getUpdates) only works when no webhook is set."""
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        """Long polling: waits up to `timeout` seconds for new messages."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        response = httpx.post(f"{self._base}/getUpdates", json=payload, timeout=timeout + 15)
        data = response.json()
        if not data.get("ok"):
            raise TelegramError(f"getUpdates failed: {data.get('description', response.status_code)}")
        return data["result"]

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
    buttons: list[Buttons | None] = field(default_factory=list)
    callbacks: list[str] = field(default_factory=list)
    fail: bool = False

    def send_message(self, chat_id: int, text: str, buttons: Buttons | None = None) -> int:
        if self.fail:
            raise TelegramError("simulated failure")
        self.sent.append((chat_id, text))
        self.buttons.append(buttons)
        return len(self.sent)

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self.callbacks.append(callback_query_id)


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

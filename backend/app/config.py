"""App settings, read from environment variables (or a .env file).

Why: secrets and per-environment values (database URL, bot token) must never
live in code. pydantic-settings reads them once and validates their types.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://dm:dm@localhost:5432/daymanager"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    anthropic_api_key: str = ""

    run_worker: bool = False
    worker_poll_seconds: float = 30.0

    # Link codes for connecting a Telegram account expire after this long.
    link_code_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()

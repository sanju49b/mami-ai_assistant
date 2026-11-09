"""App settings, read from environment variables (or a .env file).

Why: secrets and per-environment values (database URL, bot token) must never
live in code. pydantic-settings reads them once and validates their types.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://dm:dm@localhost:5432/daymanager"

    # Public HTTPS address of this backend, e.g. https://day-manager-api.onrender.com
    public_base_url: str = "http://localhost:8000"
    # Where the web app lives (for CORS and links), e.g. https://day-manager.onrender.com
    web_app_url: str = "http://localhost:3000"

    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""

    # Claude is the only AI service.
    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-5-5"
    background_model: str = "claude-haiku-5-5"
    usd_to_gbp: float = 0.79  # only used to compare spend with the GBP budget

    # WHOOP OAuth app (developer-dashboard.whoop.com).
    whoop_client_id: str = ""
    whoop_client_secret: str = ""

    # Key for encrypting stored OAuth tokens (Fernet). Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    app_encryption_key: str = ""

    run_worker: bool = False
    worker_poll_seconds: float = 30.0

    link_code_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()

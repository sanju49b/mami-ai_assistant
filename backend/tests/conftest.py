"""Test setup: a real Postgres test database, rebuilt from migrations.

Tests run against Postgres (not SQLite) because we rely on Postgres features:
ON CONFLICT, SKIP LOCKED, JSONB and timestamptz.
"""
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://dm:dm@localhost:5432/daymanager_test"
)
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "test-secret"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["RUN_WORKER"] = "false"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_engine, session_scope  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402
from app.telegram.client import FakeTelegramClient, set_telegram_client  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    yield
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with get_engine().begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
def telegram():
    fake = FakeTelegramClient()
    set_telegram_client(fake)
    yield fake
    set_telegram_client(None)


@pytest.fixture
def client(telegram):
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def db():
    with session_scope() as session:
        yield session

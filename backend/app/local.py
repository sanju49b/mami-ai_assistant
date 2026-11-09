"""Run Mami on your laptop: `bash start.sh` (which runs `python -m app.local`).

Everything happens on this computer. You chat with Mami in your browser at
http://localhost:8000. Your data is stored in your database (DATABASE_URL).
Claude (ANTHROPIC_API_KEY) is the only outside service.

What it does:
1. Checks your settings and the database, with plain-English messages.
2. Creates/updates the tables (migrations).
3. Makes sure your profile exists.
4. Starts the background worker: morning brief, evening check-in, reminders,
   day close (every day is saved, even ones the laptop was off for).
5. Starts the web app and opens it in your browser.
"""
import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.config import DEFAULT_DATABASE_URL, get_settings
from app.db import get_engine, session_scope
from app.models import TelegramLink, User, UserSettings

BACKEND = Path(__file__).resolve().parent.parent
URL = "http://localhost:8000"
# The scheduler and reminders look up "where to send" through this link row.
# In local mode it just means "show it in the web chat".
LOCAL_CHAT_ID = 1


def check_settings() -> None:
    s = get_settings()
    problems = []
    if s.database_url == DEFAULT_DATABASE_URL or not s.database_url:
        problems.append("DATABASE_URL is empty (Supabase -> Connect -> Direct -> Session pooler)")
    elif "[YOUR-PASSWORD]" in s.database_url:
        problems.append("DATABASE_URL still has [YOUR-PASSWORD] in it: put your database password there")
    if not s.anthropic_api_key:
        problems.append("ANTHROPIC_API_KEY is empty (platform.claude.com/settings/keys)")
    if problems:
        print("\nMami can't start yet. Fix these in backend/.env, save, and run start.sh again:\n")
        for p in problems:
            print(f"  - {p}")
        print()
        sys.exit(1)


def check_database() -> None:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:200]
        print("\nMami can't reach your database.\n")
        print(f"  {msg}\n")
        if "password authentication failed" in msg:
            print("  The password in DATABASE_URL is wrong. If it has symbols like @ / # ? :")
            print("  write @ as %40, / as %2F, # as %23, ? as %3F, : as %3A (or use letters and numbers only).")
        elif "resolve host" in msg or "unreachable" in msg or "translate host" in msg:
            print("  Check the address part of DATABASE_URL. Copy it from Supabase -> Connect ->")
            print("  Direct -> Session pooler (it contains pooler.supabase.com).")
        print()
        sys.exit(1)


def migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")


def ensure_owner() -> None:
    """One profile (you), plus the local 'link' the scheduler uses to reach you."""
    with session_scope() as session:
        owner = session.execute(select(User).order_by(User.created_at)).scalars().first()
        if owner is None:
            owner = User(display_name=get_settings().owner_name)
            session.add(owner)
            session.flush()
            session.add(UserSettings(user_id=owner.id))
        link = session.execute(
            select(TelegramLink).where(TelegramLink.user_id == owner.id)
        ).scalar_one_or_none()
        if link is None:
            session.add(TelegramLink(user_id=owner.id, telegram_user_id=0, chat_id=LOCAL_CHAT_ID))


def enable_local_mode() -> None:
    os.environ["LOCAL_MODE"] = "true"
    os.environ.setdefault("RUN_WORKER", "true")  # morning brief, reminders, day close
    os.environ.setdefault("WORKER_POLL_SECONDS", "20")
    get_settings.cache_clear()


def main() -> None:
    enable_local_mode()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    check_settings()
    print("Connecting to your database…")
    check_database()
    migrate()
    ensure_owner()

    print(f"\nMami is running: {URL}")
    print("Keep this window open while you use her. Press Ctrl+C to stop.\n")
    threading.Timer(1.5, lambda: webbrowser.open(URL)).start()

    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")


if __name__ == "__main__":
    main()

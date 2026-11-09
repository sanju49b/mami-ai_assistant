"""Run Mami on your own laptop: `python -m app.local` (or ./start.sh).

What it does, in one process:
1. Checks your settings (backend/.env) and gives clear messages if something is missing.
2. Creates/updates the database tables in Supabase (migrations).
3. Makes sure your profile exists, and prints a one-tap link to connect your
   Telegram the first time.
4. Telegram "long polling": asks Telegram for new messages, so no public server
   or webhook is needed. Messages sent while the laptop was off (up to 24h) are
   picked up on start.
5. Background worker: replies, reminders, morning brief, evening check-in,
   day close, weekly review, WHOOP sync.
6. Serves the web app at http://localhost:8000
"""
import logging
import sys
import threading
import time

from alembic import command
from alembic.config import Config
from pathlib import Path

import app.handlers  # noqa: F401 (registers job handlers)
from app.cli import create_user, make_link_code
from app.config import DEFAULT_DATABASE_URL, get_settings
from app.db import session_scope
from app.jobs import run_due_jobs
from app.models import TelegramLink, User
from app.scheduler import ensure_jobs
from app.telegram.client import HttpTelegramClient, TelegramError
from app.telegram.ingest import InvalidUpdate, handle_update
from app.timeutil import utc_now

log = logging.getLogger("mami")
BACKEND = Path(__file__).resolve().parent.parent


def check_settings() -> None:
    s = get_settings()
    problems = []
    if s.database_url == DEFAULT_DATABASE_URL or not s.database_url:
        problems.append("DATABASE_URL is empty (Supabase -> Connect -> Session pooler)")
    elif "[YOUR-PASSWORD]" in s.database_url:
        problems.append("DATABASE_URL still has [YOUR-PASSWORD] in it: put your database password there")
    if not s.telegram_bot_token:
        problems.append("TELEGRAM_BOT_TOKEN is empty (get it from @BotFather)")
    if not s.anthropic_api_key:
        problems.append("ANTHROPIC_API_KEY is empty (platform.claude.com/settings/keys)")
    if problems:
        print("\nMami can't start yet. Fix these in backend/.env:\n")
        for p in problems:
            print(f"  - {p}")
        print()
        sys.exit(1)


def check_database() -> None:
    from sqlalchemy import text

    from app.db import get_engine

    try:
        with get_engine().connect() as conn:
            conn.execute(text("select 1"))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).splitlines()[0][:200]
        print("\nMami can't reach your database.\n")
        print(f"  {msg}\n")
        if "password authentication failed" in msg:
            print("  The password in DATABASE_URL is wrong (reset it in Supabase -> Database settings).")
        elif "Network is unreachable" in msg or "could not translate host" in msg:
            print("  Use the Session pooler string (Supabase -> Connect -> Session pooler),")
            print("  not the direct db.xxxx.supabase.co one: that needs IPv6, which many home networks lack.")
        print()
        sys.exit(1)


def migrate() -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(cfg, "head")


def ensure_owner_and_link(bot: HttpTelegramClient) -> None:
    s = get_settings()
    with session_scope() as session:
        user_id = session.query(User.id).first()
        linked = session.query(TelegramLink.id).first() is not None
    if user_id is None:
        user_id = create_user(s.owner_name)
    else:
        user_id = user_id[0]
    if linked:
        return
    username = bot.get_me().get("username", "")
    code = make_link_code(str(user_id))
    print("\n" + "=" * 60)
    print(" Connect your Telegram to Mami (one time only):")
    print(f"   Open on your phone:  https://t.me/{username}?start={code}")
    print("   then tap START. The link works once, for 15 minutes.")
    print("=" * 60 + "\n")


def process_updates(updates: list[dict]) -> int | None:
    """Store each update (duplicates are ignored). Returns the next offset."""
    next_offset = None
    for update in updates:
        try:
            with session_scope() as session:
                status = handle_update(session, update)
            log.info("telegram update %s: %s", update.get("update_id"), status)
        except InvalidUpdate:
            log.warning("skipped malformed update")
        next_offset = int(update.get("update_id", 0)) + 1
    return next_offset


def poll_telegram(bot: HttpTelegramClient, stop: threading.Event) -> None:
    offset = None
    delay = 2
    while not stop.is_set():
        try:
            updates = bot.get_updates(offset, timeout=25)
            if updates:
                offset = process_updates(updates)
                run_due_jobs()  # reply straight away rather than at the next worker tick
            delay = 2
        except Exception as exc:  # noqa: BLE001 - never stop polling
            # Only Telegram's own message: other errors can contain the URL, which holds the token.
            reason = str(exc) if isinstance(exc, TelegramError) else type(exc).__name__
            log.warning("Can't reach Telegram right now (%s); retrying in %ss", reason, delay)
            stop.wait(delay)
            delay = min(delay * 2, 60)


def worker(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with session_scope() as session:
                ensure_jobs(session, utc_now())
            run_due_jobs()
        except Exception:  # noqa: BLE001
            log.exception("worker problem")
        stop.wait(20)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    check_settings()
    print("Connecting to your database…")
    check_database()
    migrate()
    bot = HttpTelegramClient(get_settings().telegram_bot_token)
    try:
        bot.get_me()
        bot.delete_webhook()  # polling only works when no webhook is set
    except TelegramError as exc:
        print(f"\nTelegram didn't accept the bot token ({exc}).")
        print("Check TELEGRAM_BOT_TOKEN in backend/.env (copy it again from @BotFather).\n")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\nCan't reach Telegram ({type(exc).__name__}). Are you online?\n")
        sys.exit(1)
    ensure_owner_and_link(bot)

    stop = threading.Event()
    threading.Thread(target=poll_telegram, args=(bot, stop), daemon=True).start()
    threading.Thread(target=worker, args=(stop,), daemon=True).start()

    print("Mami is running.  Telegram: message your bot.  Web app: http://localhost:8000")
    print("Press Ctrl+C to stop.\n")
    import uvicorn

    try:
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, log_level="warning")
    finally:
        stop.set()
        time.sleep(0.2)


if __name__ == "__main__":
    main()

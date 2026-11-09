"""Admin commands.

  python -m app.cli create-user --name Sri
  python -m app.cli link-code               # prints a one-time code (15 min)
  python -m app.cli set-webhook --url https://<your-service>/telegram/webhook
  python -m app.cli run-jobs                # run due jobs once
"""
import argparse
import secrets
from datetime import timedelta

from sqlalchemy import select

import app.handlers  # noqa: F401
from app.config import get_settings
from app.db import session_scope
from app.jobs import run_due_jobs
from app.models import LinkCode, User, UserSettings
from app.telegram.client import HttpTelegramClient
from app.timeutil import utc_now


def create_user(name: str) -> str:
    with session_scope() as session:
        user = User(display_name=name)
        session.add(user)
        session.flush()
        session.add(UserSettings(user_id=user.id))
        return str(user.id)


def make_link_code(user_id: str | None = None) -> str:
    with session_scope() as session:
        if user_id is None:
            users = session.execute(select(User.id)).scalars().all()
            if len(users) != 1:
                raise SystemExit("Pass --user-id (there is not exactly one user).")
            user_id = users[0]
        code = secrets.token_urlsafe(9)
        session.add(
            LinkCode(
                code=code,
                user_id=user_id,
                expires_at=utc_now() + timedelta(minutes=get_settings().link_code_minutes),
            )
        )
        return code


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user")
    p.add_argument("--name", required=True)
    p = sub.add_parser("link-code")
    p.add_argument("--user-id")
    p = sub.add_parser("set-webhook")
    p.add_argument("--url", required=True)
    sub.add_parser("run-jobs")
    args = parser.parse_args()

    if args.cmd == "create-user":
        print(create_user(args.name))
    elif args.cmd == "link-code":
        code = make_link_code(args.user_id)
        print(f"Send this to your bot within {get_settings().link_code_minutes} minutes:")
        print(f"/start {code}")
    elif args.cmd == "set-webhook":
        settings = get_settings()
        if not settings.telegram_webhook_secret:
            raise SystemExit("Set TELEGRAM_WEBHOOK_SECRET first.")
        HttpTelegramClient(settings.telegram_bot_token).set_webhook(
            args.url, settings.telegram_webhook_secret
        )
        print("Webhook set.")
    elif args.cmd == "run-jobs":
        print(run_due_jobs())


if __name__ == "__main__":
    main()

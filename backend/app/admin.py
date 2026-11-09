"""Free-hosting helpers: a one-time setup page and the keep-awake 'tick'.

Render's free plan sleeps after 15 minutes without traffic, and its free plan
has no shell. So:
- /setup?key=<TELEGRAM_WEBHOOK_SECRET> (open once in your browser): creates
  your user, registers the Telegram webhook, and gives you a one-tap link to
  connect your Telegram to Mami. Safe to open again; it never duplicates.
- /cron/tick?key=<CRON_KEY> (called by cron-job.org every 5 minutes): keeps
  the server awake and runs any due jobs in the background.
"""
import hmac
import html
import logging
import threading

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.cli import create_user, make_link_code
from app.config import get_settings, webhook_token
from app.db import session_scope
from app.models import TelegramLink, User
from app.telegram.client import HttpTelegramClient, TelegramError

log = logging.getLogger(__name__)
router = APIRouter(tags=["admin"])
_tick_lock = threading.Lock()


def _check(given: str, expected: str) -> None:
    if not expected or not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=404)  # don't reveal that the page exists


def _run_tick() -> None:
    from app.main import worker_tick

    if not _tick_lock.acquire(blocking=False):
        return  # a tick is already running
    try:
        worker_tick()
    except Exception:  # noqa: BLE001
        log.exception("tick failed")
    finally:
        _tick_lock.release()


@router.get("/cron/tick")
def cron_tick(key: str = "") -> dict[str, str]:
    _check(key, get_settings().cron_key)
    # Answer immediately (cron-job.org waits at most 30 s); work happens in the background.
    threading.Thread(target=_run_tick, daemon=True).start()
    return {"status": "ok"}


@router.get("/setup", response_class=HTMLResponse)
def setup(request: Request, key: str = "", name: str = "Sri") -> HTMLResponse:
    s = get_settings()
    _check(key, s.telegram_webhook_secret)
    base = s.public_base_url.rstrip("/") or s.render_external_url.rstrip("/") \
        or str(request.base_url).rstrip("/")
    steps: list[str] = []

    with session_scope() as session:
        user_id = session.execute(select(User.id)).scalars().first()
        linked = session.execute(select(TelegramLink)).scalars().first() is not None
    if user_id is None:
        user_id = create_user(name[:60] or "Sri")
        steps.append(f"Created your profile ({html.escape(name)}).")
    else:
        steps.append("Your profile already exists.")

    bot = HttpTelegramClient(s.telegram_bot_token)
    try:
        bot.set_webhook(f"{base}/telegram/webhook", webhook_token(s.telegram_webhook_secret))
        steps.append("Telegram webhook is set: Mami can now receive your messages.")
        username = bot.get_me().get("username", "")
        info = bot.get_webhook_info()
    except TelegramError as exc:
        return HTMLResponse(
            f"<h1>Telegram problem</h1><p>{html.escape(str(exc))}</p>"
            "<p>Check TELEGRAM_BOT_TOKEN in Render's Environment tab.</p>", status_code=502)
    steps.append(f"Bot: @{html.escape(username)}")
    steps.append("Telegram linked: <b>yes</b>" if linked else
                 "Telegram linked: <b>not yet</b> (use the button below, then tap Start)")
    if info.get("last_error_message"):
        steps.append(f"Telegram's last delivery error: {html.escape(info['last_error_message'])}")
    if info.get("pending_update_count"):
        steps.append(f"Messages waiting to be delivered: {info['pending_update_count']}")

    if linked:
        link_html = "<p>Your Telegram is already linked to Mami.</p>"
    else:
        code = make_link_code(str(user_id))
        url = f"https://t.me/{username}?start={code}"
        link_html = (
            f"<p><a class=btn href='{html.escape(url)}'>Connect my Telegram to Mami</a></p>"
            f"<p class=muted>Opens Telegram; tap Start. The link works once, for "
            f"{s.link_code_minutes} minutes. Refresh this page for a new one.</p>"
        )

    cron_url = f"{base}/cron/tick?key={s.cron_key}" if s.cron_key else "(CRON_KEY is not set)"
    body = f"""<!doctype html><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Mami setup</title>
<style>
body{{font-family:system-ui;background:#f6f3ee;color:#2b2622;max-width:36rem;margin:auto;padding:2rem 1.25rem;line-height:1.5}}
h1{{font-family:Georgia,serif;font-size:2.2rem;margin:0 0 1rem}} h2{{font-family:Georgia,serif;margin-top:2rem}}
.btn{{display:inline-block;background:#2b2622;color:#fff;padding:.8rem 1.2rem;border-radius:999px;text-decoration:none;font-weight:600}}
code{{display:block;background:#fff;border:1px solid #e7dfd5;border-radius:10px;padding:.75rem;word-break:break-all}}
.muted{{color:#75695f;font-size:.9rem}} li{{margin:.3rem 0}}
</style>
<h1>Mami setup</h1>
<ul>{''.join(f'<li>{x}</li>' for x in steps)}</ul>
<h2>1. Connect Telegram</h2>{link_html}
<h2>2. Keep Mami awake (free)</h2>
<p>At <b>cron-job.org</b> (free): sign up, then Create cronjob. Paste this URL and set it to run
<b>every 5 minutes</b>:</p><code>{html.escape(cron_url)}</code>
<p class=muted>This wakes the free server so reminders and check-ins arrive on time. Keep this URL private.</p>
<h2>3. Open the app</h2>
<p><a class=btn href='{html.escape(base)}/'>Open Mami</a></p>
<p class=muted>On iPhone: Share, then Add to Home Screen.</p>"""
    return HTMLResponse(body)

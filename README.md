# Day Manager

A personal AI day manager: Telegram + web app, one FastAPI backend, Postgres as
the single source of truth. Architecture and plan: see the
"Personal AI Day Manager — Architecture Review & Plan" doc.

## Status

| Batch | What | State |
| --- | --- | --- |
| 0 | Scaffold: FastAPI, settings, Alembic, tests, CI | Done |
| 1 | Core schema, Telegram webhook, secret check, duplicate protection, account linking, raw message storage, acknowledgement | Done |
| 2 | Job queue and worker (retries, backoff, SKIP LOCKED, idempotent sends) | Core done; quiet hours and notification caps still to do |
| 3 | AI agent with read tools | Next |

AI: Claude is the only AI service (one key: `ANTHROPIC_API_KEY`). Claude reads
text and images, not audio, so voice notes are stored and the bot asks you to use
your phone's keyboard dictation instead.

What works today: you message the bot, every message is stored before anything
else happens, duplicates are ignored, strangers are ignored, and the bot replies
"Saved." (the AI brain arrives in batch 3).

## Project layout

```
backend/
  app/
    config.py          settings from environment variables
    db.py              database engine and sessions
    models.py          tables
    timeutil.py        UTC storage, Europe/London "day" with a 04:00 day end
    jobs.py            durable background jobs (enqueue, claim, retry)
    handlers.py        what each job kind does
    telegram/
      router.py        POST /telegram/webhook (secret header check)
      ingest.py        store raw update -> link or identify -> save -> queue job
      outbound.py      send messages, never twice for the same key
      client.py        Telegram API wrapper (+ fake for tests)
    cli.py             admin commands
    main.py            FastAPI app, starts the worker thread
  alembic/             database migrations
  tests/               pytest, runs against real Postgres
```

## Run it locally

You need Python 3.12+ and Postgres 16.

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # then edit DATABASE_URL etc.

createdb daymanager && createdb daymanager_test
alembic upgrade head
pytest                        # uses daymanager_test
uvicorn app.main:app --reload
```

## Connect your Telegram (after deploying)

1. Create the bot with @BotFather (`/newbot`) and put the token in
   `TELEGRAM_BOT_TOKEN`. Generate `TELEGRAM_WEBHOOK_SECRET` with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Deploy (see below), then register the webhook:
   `python -m app.cli set-webhook --url https://<your-service>.onrender.com/telegram/webhook`
3. Create your user and a one-time link code:
   `python -m app.cli create-user --name Sri` then `python -m app.cli link-code`
4. Send the printed `/start <code>` to your bot within 15 minutes. The bot replies
   "Linked." From then on only your Telegram account is served.

## Deploy

1. Push this repo to a private personal GitHub repo.
2. Create a personal Supabase project; copy its connection string into
   `DATABASE_URL` (prefix `postgresql+psycopg://`).
3. In Render: New -> Blueprint -> pick the repo (uses `render.yaml`), then fill
   in the secret environment variables in the dashboard.
4. In Supabase, turn off the auto-generated Data API for these tables, or leave
   row-level security on (the migration enables it with no policies), so the
   only way in is through the backend.

## Tests

`pytest` covers: health, models matching migrations, day boundaries across the
UK clock changes, webhook secret checks, strangers ignored, linking (including
reused and expired codes), message storage, duplicate deliveries (spec scenario
7), voice-note storage, late-night messages counting towards the previous day,
no double replies on retried jobs, job retries and backoff (spec scenario 11),
rollback of failed jobs, recovery of abandoned jobs, and four concurrent workers
never running the same job.

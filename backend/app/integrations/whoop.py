"""WHOOP integration (developer API v2, OAuth 2.0 authorization-code flow).

Facts this relies on (developer.whoop.com, checked Nov 2026):
- Authorize: https://api.prod.whoop.com/oauth/oauth2/auth
- Token:     https://api.prod.whoop.com/oauth/oauth2/token
- The 'offline' scope is required to get a refresh token. Each refresh returns
  a NEW refresh token, so we always save it (rotation).
- Data base URL: https://api.prod.whoop.com/developer/v2 ; collection
  endpoints page with limit (max 25) and nextToken.
- Recovery appears only after a sleep finishes; cycles run wake-to-wake.
- Cycles include step_count since Sept 2026 (null if not worn).

WHOOP data is stored as measured source data; the agent may suggest, but never
changes goals because of it.
"""
import logging
import secrets
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.common import get_settings_row
from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.models import IntegrationConnection, OAuthState, WhoopDay, WhoopWorkout
from app.timeutil import local_date, utc_now

log = logging.getLogger(__name__)

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API_BASE = "https://api.prod.whoop.com/developer/v2"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile"
PROVIDER = "whoop"

# Tests replace this with an httpx.MockTransport.
transport: httpx.BaseTransport | None = None


class WhoopError(RuntimeError):
    pass


def _http() -> httpx.Client:
    return httpx.Client(timeout=20, transport=transport)


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + "/whoop/callback"


def is_configured() -> bool:
    s = get_settings()
    return bool(s.whoop_client_id and s.whoop_client_secret and s.app_encryption_key)


def start_connect(session: Session, user_id: uuid.UUID) -> str:
    """Create a one-time state and return the WHOOP sign-in URL."""
    state = secrets.token_urlsafe(24)
    session.add(OAuthState(state=state, user_id=user_id, provider=PROVIDER,
                           expires_at=utc_now() + timedelta(minutes=15)))
    params = {
        "client_id": get_settings().whoop_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _save_tokens(session: Session, user_id: uuid.UUID, data: dict[str, Any]) -> IntegrationConnection:
    conn = session.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.user_id == user_id, IntegrationConnection.provider == PROVIDER
        ).with_for_update()
    ).scalar_one_or_none()
    expires_at = utc_now() + timedelta(seconds=int(data.get("expires_in", 3600)))
    if conn is None:
        conn = IntegrationConnection(user_id=user_id, provider=PROVIDER,
                                     access_token_enc=encrypt(data["access_token"]))
        session.add(conn)
    conn.access_token_enc = encrypt(data["access_token"])
    if data.get("refresh_token"):
        conn.refresh_token_enc = encrypt(data["refresh_token"])
    conn.expires_at = expires_at
    conn.scopes = data.get("scope")
    conn.status = "active"
    conn.updated_at = utc_now()
    session.flush()
    return conn


def finish_connect(session: Session, state: str, code: str) -> uuid.UUID:
    """Validate the state, exchange the code for tokens, store them encrypted."""
    row = session.get(OAuthState, state, with_for_update=True)
    now = utc_now()
    if row is None or row.provider != PROVIDER or row.used_at is not None or row.expires_at < now:
        raise WhoopError("This WHOOP link is invalid or expired. Send /whoop to get a new one.")
    row.used_at = now
    s = get_settings()
    with _http() as http:
        resp = http.post(TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": s.whoop_client_id,
            "client_secret": s.whoop_client_secret,
            "redirect_uri": redirect_uri(),
        })
    if resp.status_code != 200:
        raise WhoopError(f"WHOOP token exchange failed ({resp.status_code})")
    _save_tokens(session, row.user_id, resp.json())
    return row.user_id


def _refresh(session: Session, conn: IntegrationConnection) -> None:
    if not conn.refresh_token_enc:
        conn.status = "needs_reconnect"
        raise WhoopError("No refresh token; reconnect WHOOP with /whoop")
    s = get_settings()
    with _http() as http:
        resp = http.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": decrypt(conn.refresh_token_enc),
            "client_id": s.whoop_client_id,
            "client_secret": s.whoop_client_secret,
            "scope": "offline",
        })
    if resp.status_code != 200:
        conn.status = "needs_reconnect"
        raise WhoopError(f"WHOOP token refresh failed ({resp.status_code})")
    _save_tokens(session, conn.user_id, resp.json())


class WhoopAPI:
    def __init__(self, session: Session, conn: IntegrationConnection) -> None:
        self.session = session
        self.conn = conn

    def _token(self) -> str:
        if self.conn.expires_at is None or self.conn.expires_at - utc_now() < timedelta(seconds=60):
            _refresh(self.session, self.conn)
        return decrypt(self.conn.access_token_enc)

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        for attempt in (1, 2):
            with _http() as http:
                resp = http.get(f"{API_BASE}{path}", params=params,
                                headers={"Authorization": f"Bearer {self._token()}"})
            if resp.status_code == 401 and attempt == 1:
                _refresh(self.session, self.conn)
                continue
            if resp.status_code == 429:
                raise WhoopError("WHOOP rate limit reached; will retry later")
            if resp.status_code >= 400:
                raise WhoopError(f"WHOOP GET {path} failed ({resp.status_code})")
            return resp.json()
        raise WhoopError("WHOOP authorisation failed")

    def collection(self, path: str, start: datetime, end: datetime) -> Iterator[dict[str, Any]]:
        params: dict[str, Any] = {"start": _iso(start), "end": _iso(end), "limit": 25}
        for _ in range(20):  # safety bound
            page = self.get(path, params)
            yield from page.get("records", [])
            token = page.get("next_token") or page.get("nextToken")
            if not token:
                return
            params["nextToken"] = token


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _num(d: dict | None, *keys: str):
    for k in keys:
        if d and d.get(k) is not None:
            return d[k]
    return None


def get_connection(session: Session, user_id: uuid.UUID) -> IntegrationConnection | None:
    return session.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.user_id == user_id,
            IntegrationConnection.provider == PROVIDER,
            IntegrationConnection.status == "active",
        )
    ).scalar_one_or_none()


def sync(session: Session, user_id: uuid.UUID, days: int = 3) -> dict[str, int]:
    """Pull the last few days of cycles, recovery, sleep and workouts (idempotent)."""
    conn = get_connection(session, user_id)
    if conn is None:
        return {"skipped": 1}
    settings = get_settings_row(session, user_id)
    api = WhoopAPI(session, conn)
    end = utc_now()
    start = end - timedelta(days=days)
    counts = {"cycles": 0, "recoveries": 0, "sleeps": 0, "workouts": 0}

    for c in api.collection("/cycle", start, end):
        cstart = _parse(c.get("start"))
        if cstart is None:
            continue
        score = c.get("score") or {}
        values = {
            "user_id": user_id,
            "cycle_id": str(c["id"]),
            "local_date": local_date(cstart, settings.timezone, settings.day_end_hour),
            "cycle_start": cstart,
            "cycle_end": _parse(c.get("end")),
            "strain": score.get("strain"),
            "kilojoule": score.get("kilojoule"),
            "average_heart_rate": score.get("average_heart_rate"),
            "max_heart_rate": score.get("max_heart_rate"),
            "step_count": _num(c, "step_count") if _num(c, "step_count") is not None
            else _num(score, "step_count"),
            "raw": {"cycle": c},
            "updated_at": utc_now(),
        }
        stmt = insert(WhoopDay).values(**values)
        update_cols = {k: stmt.excluded[k] for k in values if k not in ("user_id", "cycle_id", "raw")}
        update_cols["raw"] = WhoopDay.raw.op("||")(stmt.excluded.raw)
        session.execute(stmt.on_conflict_do_update(constraint="uq_whoop_cycle", set_=update_cols))
        counts["cycles"] += 1

    for r in api.collection("/recovery", start, end):
        score = r.get("score") or {}
        day = session.execute(select(WhoopDay).where(
            WhoopDay.user_id == user_id, WhoopDay.cycle_id == str(r.get("cycle_id")))
        ).scalar_one_or_none()
        if day is None:
            continue
        day.recovery_score = score.get("recovery_score")
        day.hrv_rmssd_milli = score.get("hrv_rmssd_milli")
        day.resting_heart_rate = score.get("resting_heart_rate")
        day.raw = {**day.raw, "recovery": r}
        counts["recoveries"] += 1

    for sl in api.collection("/activity/sleep", start, end):
        if sl.get("nap"):
            continue
        score = sl.get("score") or {}
        stages = score.get("stage_summary") or {}
        asleep_ms = sum(stages.get(k) or 0 for k in (
            "total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli",
            "total_rem_sleep_time_milli"))
        day = None
        if sl.get("cycle_id") is not None:
            day = session.execute(select(WhoopDay).where(
                WhoopDay.user_id == user_id, WhoopDay.cycle_id == str(sl["cycle_id"]))
            ).scalar_one_or_none()
        if day is None:
            continue
        day.sleep_performance = score.get("sleep_performance_percentage")
        day.sleep_hours = round(asleep_ms / 3_600_000, 2) if asleep_ms else None
        day.raw = {**day.raw, "sleep": sl}
        counts["sleeps"] += 1

    for w in api.collection("/activity/workout", start, end):
        wstart = _parse(w.get("start"))
        if wstart is None:
            continue
        score = w.get("score") or {}
        values = {
            "user_id": user_id,
            "workout_id": str(w["id"]),
            "local_date": local_date(wstart, settings.timezone, settings.day_end_hour),
            "sport_name": w.get("sport_name"),
            "started_at": wstart,
            "ended_at": _parse(w.get("end")),
            "strain": score.get("strain"),
            "average_heart_rate": score.get("average_heart_rate"),
            "max_heart_rate": score.get("max_heart_rate"),
            "kilojoule": score.get("kilojoule"),
            "raw": w,
        }
        stmt = insert(WhoopWorkout).values(**values)
        session.execute(stmt.on_conflict_do_update(
            constraint="uq_whoop_workout",
            set_={k: stmt.excluded[k] for k in values if k not in ("user_id", "workout_id")}))
        counts["workouts"] += 1

    conn.last_synced_at = utc_now()
    return counts

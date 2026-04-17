"""
Garmin Connect API routes — connect/disconnect, sync trigger, health data.
"""
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.garmin.client import GarminClient
from app.garmin.crypto import decrypt_for_athlete, encrypt_for_athlete
from app.models.schema import Athlete, GarminCredentials, GarminDailyHealth
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/garmin", tags=["garmin"])


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


def _mask_email(email: str) -> str:
    parts = email.split("@")
    if len(parts) != 2:
        return "***"
    local = parts[0]
    masked = local[:2] + "***" if len(local) > 2 else "***"
    return f"{masked}@{parts[1]}"


# ---------- Connect / Disconnect ----------

class GarminConnectRequest(BaseModel):
    email: str
    password: str


@router.post("/connect")
async def connect_garmin(
    body: GarminConnectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Test Garmin login, then store encrypted credentials."""
    athlete_id = _get_athlete_id(request)

    # Load athlete row — needed to derive the per-user encryption key.
    athlete = (
        await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    ).scalar_one_or_none()
    if athlete is None:
        raise HTTPException(status_code=404, detail="Athlete not found")

    # Validate credentials by attempting a real Garmin login.
    client = GarminClient(body.email, body.password, athlete_id=athlete_id)
    try:
        await client.login()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Garmin login failed: {e}") from e

    # Encrypt with the athlete's derived key; ensures salt exists.
    email_ct = encrypt_for_athlete(athlete, body.email)
    password_ct = encrypt_for_athlete(athlete, body.password)

    now = int(time.time())
    await db.execute(
        text(
            """
            INSERT INTO garmin_credentials
                (athlete_id, email_encrypted, password_encrypted, is_connected, created_at, updated_at)
            VALUES (:aid, :email, :password, 1, :now, :now)
            ON CONFLICT(athlete_id) DO UPDATE SET
                email_encrypted = excluded.email_encrypted,
                password_encrypted = excluded.password_encrypted,
                is_connected = 1,
                last_error = NULL,
                updated_at = excluded.updated_at
            """
        ),
        {"aid": athlete_id, "email": email_ct, "password": password_ct, "now": now},
    )
    await db.commit()

    return {"connected": True, "email": _mask_email(body.email)}


@router.post("/disconnect")
async def disconnect_garmin(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Remove credentials and cached tokens."""
    athlete_id = _get_athlete_id(request)

    await db.execute(
        text("DELETE FROM garmin_credentials WHERE athlete_id = :aid"),
        {"aid": athlete_id},
    )
    await db.commit()

    # Only clear this athlete's cached tokens.
    GarminClient.clear_tokens_for(athlete_id)

    return {"disconnected": True}


# ---------- Status ----------

@router.get("/status")
async def garmin_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)

    cred_result = await db.execute(
        select(GarminCredentials).where(GarminCredentials.athlete_id == athlete_id)
    )
    cred = cred_result.scalar_one_or_none()

    if not cred:
        return {"connected": False}

    # Count synced days
    count_result = await db.execute(
        select(func.count()).select_from(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id
        )
    )
    days_synced = count_result.scalar() or 0

    masked = "***"
    try:
        athlete = (
            await db.execute(select(Athlete).where(Athlete.id == athlete_id))
        ).scalar_one_or_none()
        if athlete is not None:
            masked = _mask_email(decrypt_for_athlete(athlete, cred.email_encrypted))
    except Exception:
        pass

    return {
        "connected": cred.is_connected == 1,
        "email": masked,
        "last_sync_date": cred.last_sync_date,
        "last_error": cred.last_error,
        "days_synced": days_synced,
    }


# ---------- Sync trigger ----------

@router.post("/sync")
async def trigger_garmin_sync(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manual sync of Garmin health data (last 14 days)."""
    import logging

    from app.garmin.sync import sync_garmin_health

    log = logging.getLogger("racingplanner.garmin.sync")
    athlete_id = _get_athlete_id(request)
    try:
        count = await sync_garmin_health(db, athlete_id, days=14)
    except Exception as e:  # noqa: BLE001
        # Without this, an uncaught 500 bypasses the CORS middleware and the
        # browser reports the frustrating "Failed to fetch" instead of the
        # actual reason. Log server-side too so the cause is visible in logs.
        log.exception("Garmin sync failed for athlete %s", athlete_id)
        raise HTTPException(
            status_code=500,
            detail=f"Garmin sync failed: {type(e).__name__}: {e}",
        ) from e
    return {"synced_days": count}


# ---------- Health data endpoints ----------

@router.get("/health")
async def get_health(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    if not start:
        start = (date.today() - timedelta(days=30)).isoformat()
    if not end:
        end = date.today().isoformat()

    result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start,
            GarminDailyHealth.date <= end,
        ).order_by(GarminDailyHealth.date)
    )
    rows = result.scalars().all()
    return [_health_row_to_dict(r) for r in rows]


@router.get("/health/latest")
async def get_health_latest(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
        ).order_by(desc(GarminDailyHealth.date)).limit(1)
    )
    row = result.scalar_one_or_none()
    if not row:
        return None
    return _health_row_to_dict(row)


@router.get("/health/trends")
async def get_health_trends(
    request: Request,
    days: int = 90,
    db: AsyncSession = Depends(get_db),
):
    """Arrays suitable for chart rendering."""
    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(days=days)).isoformat()

    result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start,
        ).order_by(GarminDailyHealth.date)
    )
    rows = result.scalars().all()

    return {
        "dates": [r.date for r in rows],
        "hrv": [r.hrv_last_night for r in rows],
        "hrv_weekly_avg": [r.hrv_weekly_avg for r in rows],
        "sleep_hours": [
            round(r.sleep_duration_sec / 3600, 1) if r.sleep_duration_sec else None
            for r in rows
        ],
        "sleep_score": [r.sleep_score for r in rows],
        "resting_hr": [r.resting_hr for r in rows],
        "body_battery": [r.body_battery_latest for r in rows],
        "stress": [r.stress_avg for r in rows],
        "training_readiness": [r.training_readiness for r in rows],
        "vo2max": [r.vo2max_running for r in rows],
    }


def _health_row_to_dict(r: GarminDailyHealth) -> dict:
    return {
        "date": r.date,
        "hrv_weekly_avg": r.hrv_weekly_avg,
        "hrv_last_night": r.hrv_last_night,
        "hrv_status": r.hrv_status,
        "sleep_duration_sec": r.sleep_duration_sec,
        "sleep_hours": round(r.sleep_duration_sec / 3600, 1) if r.sleep_duration_sec else None,
        "sleep_score": r.sleep_score,
        "sleep_deep_sec": r.sleep_deep_sec,
        "sleep_light_sec": r.sleep_light_sec,
        "sleep_rem_sec": r.sleep_rem_sec,
        "sleep_awake_sec": r.sleep_awake_sec,
        "resting_hr": r.resting_hr,
        "body_battery_high": r.body_battery_high,
        "body_battery_low": r.body_battery_low,
        "body_battery_latest": r.body_battery_latest,
        "stress_avg": r.stress_avg,
        "training_readiness": r.training_readiness,
        "training_status": r.training_status,
        "vo2max_running": r.vo2max_running,
    }

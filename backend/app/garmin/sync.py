"""
Garmin health data sync — fetches daily recovery metrics and stores them.
"""
import asyncio
import json
import logging
import time
from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.garmin.client import GarminClient
from app.garmin.crypto import decrypt_for_athlete
from app.models.schema import Athlete, GarminCredentials, GarminDailyHealth

log = logging.getLogger("racingplanner.garmin.sync")

# ---------------------------------------------------------------------------
# Response parsers — each Garmin endpoint returns different structures.
# These extract the fields we care about, returning {} on unexpected shapes.
# ---------------------------------------------------------------------------

def _int_or_none(v):
    """Coerce a value to int for Integer DB columns.

    Garmin occasionally returns floats (e.g. 62.0) where an Integer column is
    expected — asyncpg rejects that with `invalid input for query argument`.
    Truthy-cast here so any numeric-ish value becomes a clean int, and anything
    else becomes None.
    """
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _parse_heart_rates(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "resting_hr": data.get("restingHeartRate"),
    }


def _parse_hrv(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    summary = data.get("hrvSummary") or data
    return {
        "hrv_weekly_avg": summary.get("weeklyAvg"),
        "hrv_last_night": summary.get("lastNight") or summary.get("lastNightAvg"),
        "hrv_status": summary.get("status") or summary.get("startTimestampLocal"),
    }


def _parse_sleep(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    daily = data.get("dailySleepDTO") or data
    return {
        "sleep_duration_sec": daily.get("sleepTimeSeconds") or daily.get("totalSleepTimeInSeconds"),
        "sleep_score": daily.get("sleepScores", {}).get("overall", {}).get("value")
                       if isinstance(daily.get("sleepScores"), dict) else None,
        "sleep_deep_sec": daily.get("deepSleepSeconds"),
        "sleep_light_sec": daily.get("lightSleepSeconds"),
        "sleep_rem_sec": daily.get("remSleepSeconds"),
        "sleep_awake_sec": daily.get("awakeSleepSeconds"),
    }


def _parse_stats(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "resting_hr": data.get("restingHeartRate"),
        "stress_avg": data.get("averageStressLevel"),
        "vo2max_running": (
            data.get("currentVO2Max")
            or data.get("vo2Max")
            or data.get("currentVO2MaxRunning")
        ),
    }


def _parse_body_battery(data) -> dict:
    """Body battery comes as a list of timeline entries or a dict with a list."""
    entries = data if isinstance(data, list) else []
    if isinstance(data, dict):
        entries = data.get("bodyBatteryValuesArray") or data.get("data") or []
    if not entries:
        return {}
    values = []
    for entry in entries:
        val = None
        if isinstance(entry, dict):
            val = entry.get("value") or entry.get("bodyBatteryValue")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            val = entry[1]
        if val is not None and isinstance(val, (int, float)):
            values.append(int(val))
    if not values:
        return {}
    return {
        "body_battery_high": max(values),
        "body_battery_low": min(values),
        "body_battery_latest": values[-1],
    }


def _parse_training_readiness(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "training_readiness": data.get("score") or data.get("readinessScore"),
    }


def _parse_training_status(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    return {
        "training_status": data.get("trainingStatus") or data.get("currentTrainingStatus"),
    }


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

async def sync_garmin_health(
    db: AsyncSession,
    athlete_id: int,
    days: int = 14,
    progress_queue: asyncio.Queue | None = None,
) -> int:
    """
    Fetch daily health data from Garmin for the last *days* days.
    Returns the count of days synced.
    """
    async def emit(msg: str):
        if progress_queue:
            await progress_queue.put(msg)

    # 1. Load credentials + athlete (needed for per-user decryption key)
    cred_result = await db.execute(
        select(GarminCredentials).where(
            GarminCredentials.athlete_id == athlete_id,
            GarminCredentials.is_connected == 1,
        )
    )
    cred = cred_result.scalar_one_or_none()
    if not cred:
        return 0

    athlete = (
        await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    ).scalar_one_or_none()
    if not athlete:
        return 0

    # 2. Decrypt and login
    try:
        email = decrypt_for_athlete(athlete, cred.email_encrypted)
        password = decrypt_for_athlete(athlete, cred.password_encrypted)
        client = GarminClient(email, password, athlete_id=athlete_id)
        await client.login()
    except Exception as e:
        cred.is_connected = 0
        cred.last_error = str(e)[:500]
        cred.updated_at = int(time.time())
        await db.flush()
        await emit(f"  Garmin auth failed: {e}")
        return 0

    # 3. Loop over dates
    today = date.today()
    synced_count = 0
    six_hours_ago = int(time.time()) - 6 * 3600

    for offset in range(days):
        d = today - timedelta(days=offset)
        d_str = d.isoformat()

        # Check if already synced recently
        existing = await db.execute(
            select(GarminDailyHealth).where(
                GarminDailyHealth.date == d_str,
                GarminDailyHealth.athlete_id == athlete_id,
            )
        )
        row = existing.scalar_one_or_none()
        if row and row.updated_at and row.updated_at > six_hours_ago:
            continue  # skip fresh data

        # Fetch all metrics for this date, each wrapped in try/except
        merged: dict = {}
        raw_payloads: dict = {}

        for name, fetch_fn, parse_fn in [
            ("heart_rates", client.get_heart_rates, _parse_heart_rates),
            ("hrv", client.get_hrv_data, _parse_hrv),
            ("sleep", client.get_sleep_data, _parse_sleep),
            ("stats", client.get_stats, _parse_stats),
            ("body_battery", client.get_body_battery, _parse_body_battery),
            ("training_readiness", client.get_training_readiness, _parse_training_readiness),
            ("training_status", client.get_training_status, _parse_training_status),
        ]:
            try:
                raw = await fetch_fn(d_str)
                raw_payloads[name] = raw
                parsed = parse_fn(raw)
                # Only overwrite if parsed has non-None values
                for k, v in parsed.items():
                    if v is not None:
                        merged[k] = v
            except Exception as e:  # noqa: BLE001
                # Stays Exception — python-garminconnect raises a half-dozen
                # undocumented types (Garmin*Error, bare requests errors, etc.)
                # and we genuinely don't want a per-endpoint failure to
                # abort the sync. But LOG it with context so the pattern is
                # visible in Render logs instead of invisible.
                log.info(
                    "garmin %s skipped for athlete=%s date=%s: %s",
                    name, athlete_id, d_str, type(e).__name__,
                )
            await asyncio.sleep(0.5)  # rate limit

        if not merged:
            continue  # no data for this date

        # Upsert row
        now = int(time.time())
        await db.execute(text("""
            INSERT INTO garmin_daily_health
                (date, athlete_id, hrv_weekly_avg, hrv_last_night, hrv_status,
                 sleep_duration_sec, sleep_score, sleep_deep_sec, sleep_light_sec,
                 sleep_rem_sec, sleep_awake_sec, resting_hr,
                 body_battery_high, body_battery_low, body_battery_latest,
                 stress_avg, training_readiness, training_status,
                 vo2max_running, raw_json, updated_at)
            VALUES
                (:date, :athlete_id, :hrv_weekly_avg, :hrv_last_night, :hrv_status,
                 :sleep_duration_sec, :sleep_score, :sleep_deep_sec, :sleep_light_sec,
                 :sleep_rem_sec, :sleep_awake_sec, :resting_hr,
                 :body_battery_high, :body_battery_low, :body_battery_latest,
                 :stress_avg, :training_readiness, :training_status,
                 :vo2max_running, :raw_json, :updated_at)
            ON CONFLICT(date, athlete_id) DO UPDATE SET
                hrv_weekly_avg = COALESCE(excluded.hrv_weekly_avg, garmin_daily_health.hrv_weekly_avg),
                hrv_last_night = COALESCE(excluded.hrv_last_night, garmin_daily_health.hrv_last_night),
                hrv_status = COALESCE(excluded.hrv_status, garmin_daily_health.hrv_status),
                sleep_duration_sec = COALESCE(excluded.sleep_duration_sec, garmin_daily_health.sleep_duration_sec),
                sleep_score = COALESCE(excluded.sleep_score, garmin_daily_health.sleep_score),
                sleep_deep_sec = COALESCE(excluded.sleep_deep_sec, garmin_daily_health.sleep_deep_sec),
                sleep_light_sec = COALESCE(excluded.sleep_light_sec, garmin_daily_health.sleep_light_sec),
                sleep_rem_sec = COALESCE(excluded.sleep_rem_sec, garmin_daily_health.sleep_rem_sec),
                sleep_awake_sec = COALESCE(excluded.sleep_awake_sec, garmin_daily_health.sleep_awake_sec),
                resting_hr = COALESCE(excluded.resting_hr, garmin_daily_health.resting_hr),
                body_battery_high = COALESCE(excluded.body_battery_high, garmin_daily_health.body_battery_high),
                body_battery_low = COALESCE(excluded.body_battery_low, garmin_daily_health.body_battery_low),
                body_battery_latest = COALESCE(excluded.body_battery_latest, garmin_daily_health.body_battery_latest),
                stress_avg = COALESCE(excluded.stress_avg, garmin_daily_health.stress_avg),
                training_readiness = COALESCE(excluded.training_readiness, garmin_daily_health.training_readiness),
                training_status = COALESCE(excluded.training_status, garmin_daily_health.training_status),
                vo2max_running = COALESCE(excluded.vo2max_running, garmin_daily_health.vo2max_running),
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
        """), {
            "date": d_str,
            "athlete_id": athlete_id,
            "hrv_weekly_avg": merged.get("hrv_weekly_avg"),
            "hrv_last_night": merged.get("hrv_last_night"),
            "hrv_status": merged.get("hrv_status"),
            "sleep_duration_sec": _int_or_none(merged.get("sleep_duration_sec")),
            "sleep_score": merged.get("sleep_score"),
            "sleep_deep_sec": _int_or_none(merged.get("sleep_deep_sec")),
            "sleep_light_sec": _int_or_none(merged.get("sleep_light_sec")),
            "sleep_rem_sec": _int_or_none(merged.get("sleep_rem_sec")),
            "sleep_awake_sec": _int_or_none(merged.get("sleep_awake_sec")),
            "resting_hr": _int_or_none(merged.get("resting_hr")),
            "body_battery_high": _int_or_none(merged.get("body_battery_high")),
            "body_battery_low": _int_or_none(merged.get("body_battery_low")),
            "body_battery_latest": _int_or_none(merged.get("body_battery_latest")),
            "stress_avg": _int_or_none(merged.get("stress_avg")),
            "training_readiness": merged.get("training_readiness"),
            "training_status": merged.get("training_status"),
            "vo2max_running": merged.get("vo2max_running"),
            "raw_json": json.dumps(raw_payloads, default=str)[:10000],
            "updated_at": now,
        })

        synced_count += 1
        if offset % 3 == 0:
            await emit(f"  Garmin health: {offset + 1}/{days} days")

    # ─ Slow-changing physiology: lactate threshold + endurance score ────────
    # These update weekly at best, so we fetch once (latest-only) and write
    # to today's row. Each call is independently wrapped so a Garmin endpoint
    # deprecation doesn't break the whole sync.
    lt_speed = None
    lt_hr = None
    endurance = None

    try:
        lt_raw = await client.get_lactate_threshold()
        shr = (lt_raw or {}).get("speed_and_heart_rate") or {}
        lt_speed = shr.get("speed")               # m/s
        lt_hr = shr.get("heartRate") or shr.get("hearRate")  # Garmin's legacy typo
    except Exception as e:
        await emit(f"  LT threshold skipped: {type(e).__name__}")
    await asyncio.sleep(0.3)

    today_str = today.isoformat()
    try:
        es_raw = await client.get_endurance_score(today_str)
        if isinstance(es_raw, dict):
            # Field name varies across library versions — try the obvious ones.
            endurance = (
                es_raw.get("overallScore")
                or es_raw.get("enduranceScore")
                or es_raw.get("score")
            )
    except Exception as e:
        await emit(f"  Endurance score skipped: {type(e).__name__}")

    if lt_speed is not None or lt_hr is not None or endurance is not None:
        now = int(time.time())
        await db.execute(text("""
            INSERT INTO garmin_daily_health
                (date, athlete_id, lactate_threshold_speed_ms,
                 lactate_threshold_hr, endurance_score, updated_at)
            VALUES
                (:date, :athlete_id, :lt_speed, :lt_hr, :endurance, :updated_at)
            ON CONFLICT(date, athlete_id) DO UPDATE SET
                lactate_threshold_speed_ms = COALESCE(excluded.lactate_threshold_speed_ms, garmin_daily_health.lactate_threshold_speed_ms),
                lactate_threshold_hr = COALESCE(excluded.lactate_threshold_hr, garmin_daily_health.lactate_threshold_hr),
                endurance_score = COALESCE(excluded.endurance_score, garmin_daily_health.endurance_score),
                updated_at = excluded.updated_at
        """), {
            "date": today_str,
            "athlete_id": athlete_id,
            "lt_speed": float(lt_speed) if lt_speed is not None else None,
            "lt_hr": _int_or_none(lt_hr),
            "endurance": float(endurance) if endurance is not None else None,
            "updated_at": now,
        })

    # Update credentials
    cred.last_sync_date = today.isoformat()
    cred.last_error = None
    cred.updated_at = int(time.time())
    await db.flush()

    return synced_count

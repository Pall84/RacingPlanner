import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.database import SessionLocal as async_session
from app.models.schema import (
    Activity,
    ActivityMetrics,
    ActivityStream,
    Athlete,
    GarminDailyHealth,
    KmSplit,
    Lap,
)
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


def _pace_to_str(sec_per_km: float | None) -> str | None:
    if sec_per_km is None:
        return None
    mins = int(sec_per_km // 60)
    secs = int(sec_per_km % 60)
    return f"{mins}:{secs:02d}"


def _activity_row(activity: Activity, metrics: ActivityMetrics | None) -> dict:
    d = {
        "id": activity.id,
        "name": activity.name,
        "type": activity.sport_type or activity.type,
        "start_date": activity.start_date,
        "start_date_local": activity.start_date_local,
        "distance_m": activity.distance,
        "distance_km": round(activity.distance / 1000, 2) if activity.distance else None,
        "moving_time": activity.moving_time,
        "elapsed_time": activity.elapsed_time,
        "elevation_gain": activity.total_elevation_gain,
        "average_heartrate": activity.average_heartrate,
        "max_heartrate": activity.max_heartrate,
        "average_cadence_raw": activity.average_cadence,
        "average_cadence_spm": round(activity.average_cadence * 2, 1) if activity.average_cadence else None,
        "average_watts": activity.average_watts,
        "trainer": bool(activity.trainer),
        "treadmill_corrected": bool(activity.treadmill_corrected),
        "has_heartrate": bool(activity.has_heartrate),
        "map_polyline": activity.map_summary_polyline,
        "streams_synced": bool(activity.streams_synced),
        "metrics_computed": bool(activity.metrics_computed),
        "is_race": bool(activity.is_race),
    }
    if activity.average_speed and activity.average_speed > 0:
        d["avg_pace_str"] = _pace_to_str(1000 / activity.average_speed)
    if metrics:
        d["avg_pace_sec_per_km"] = metrics.avg_pace_sec_per_km
        d["avg_pace_str"] = _pace_to_str(metrics.avg_pace_sec_per_km)
        d["avg_gap_sec_per_km"] = metrics.avg_gap_sec_per_km
        d["pace_decoupling_pct"] = metrics.pace_decoupling_pct
        d["trimp"] = round(metrics.trimp_total, 1) if metrics.trimp_total else None
        d["rss"] = round(metrics.rss, 1) if metrics.rss else None
        d["cadence_avg"] = round(metrics.cadence_avg, 1) if metrics.cadence_avg else None
        d["normalized_power"] = metrics.normalized_power
        d["intensity_factor"] = metrics.intensity_factor
        d["workout_type"] = metrics.workout_type
        d["estimated_vdot"] = round(metrics.estimated_vdot, 1) if metrics.estimated_vdot else None
    return d


class LapCorrectionRequest(BaseModel):
    corrected_distance_km: float | None = None   # None → reset correction
    corrected_elevation_gain: float | None = None  # None → reset correction


def _lap_row(lap: Lap) -> dict:
    eff_dist = lap.corrected_distance if lap.corrected_distance is not None else lap.distance
    eff_elev = lap.corrected_elevation_gain if lap.corrected_elevation_gain is not None else lap.total_elevation_gain
    # Compute effective pace from effective distance + time (more robust than stored value)
    eff_pace = None
    if lap.moving_time and lap.moving_time > 0 and eff_dist:
        eff_pace = lap.moving_time / (eff_dist / 1000.0)
    return {
        "lap_index": lap.lap_index,
        "name": lap.name,
        "distance": lap.distance,
        "total_elevation_gain": lap.total_elevation_gain,
        "corrected_distance": lap.corrected_distance,
        "corrected_elevation_gain": lap.corrected_elevation_gain,
        "effective_distance": eff_dist,
        "effective_elevation_gain": eff_elev,
        "is_corrected": lap.corrected_distance is not None or lap.corrected_elevation_gain is not None,
        "moving_time": lap.moving_time,
        "elapsed_time": lap.elapsed_time,
        "average_speed": lap.average_speed,
        "pace_sec_per_km": eff_pace,
        "pace_str": _pace_to_str(eff_pace),
        "average_heartrate": lap.average_heartrate,
        "max_heartrate": lap.max_heartrate,
        "average_cadence_spm": round(lap.average_cadence * 2, 1) if lap.average_cadence else None,
    }


@router.get("")
async def list_activities(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    races_only: bool = False,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    # Filter condition is shared between the page query and the count query
    # so the total reflects the *filtered* set, which is what infinite scroll
    # needs to decide whether there's more to load.
    where_clauses = [Activity.athlete_id == athlete_id]
    if races_only:
        where_clauses.append(Activity.is_race == 1)

    result = await db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(*where_clauses)
        .order_by(desc(Activity.start_date))
        .limit(limit)
        .offset(offset)
    )
    rows = result.all()

    count_result = await db.execute(
        select(func.count()).select_from(Activity).where(*where_clauses)
    )
    total = count_result.scalar()

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "races_only": races_only,
        "activities": [_activity_row(act, met) for act, met in rows],
    }


@router.get("/{activity_id}")
async def get_activity(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404)
    activity, metrics = row
    return _activity_row(activity, metrics)


@router.get("/{activity_id}/streams")
async def get_streams(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    # Verify ownership
    act_result = await db.execute(
        select(Activity.id).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    if not act_result.scalar_one_or_none():
        raise HTTPException(status_code=404)

    result = await db.execute(
        select(ActivityStream).where(ActivityStream.activity_id == activity_id)
    )
    streams = result.scalars().all()
    return {row.stream_type: json.loads(row.data_json) for row in streams}


@router.get("/{activity_id}/km_splits")
async def get_km_splits(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    act_result = await db.execute(
        select(Activity.id).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    if not act_result.scalar_one_or_none():
        raise HTTPException(status_code=404)

    result = await db.execute(
        select(KmSplit)
        .where(KmSplit.activity_id == activity_id)
        .order_by(KmSplit.km_index)
    )
    splits = result.scalars().all()
    return [
        {
            "km_index": s.km_index,
            "distance_m": s.distance_m,
            "duration_sec": s.duration_sec,
            "pace_sec_per_km": s.pace_sec_per_km,
            "pace_str": _pace_to_str(s.pace_sec_per_km),
            "gap_sec_per_km": s.gap_sec_per_km,
            "gap_str": _pace_to_str(s.gap_sec_per_km),
            "avg_hr": s.avg_hr,
            "avg_cadence": s.avg_cadence,
            "elevation_gain": s.elevation_gain,
            "elevation_loss": s.elevation_loss,
            "avg_grade_pct": s.avg_grade_pct,
        }
        for s in splits
    ]


@router.get("/{activity_id}/laps")
async def get_laps(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    act_result = await db.execute(
        select(Activity.id).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    if not act_result.scalar_one_or_none():
        raise HTTPException(status_code=404)

    result = await db.execute(
        select(Lap).where(Lap.activity_id == activity_id).order_by(Lap.lap_index)
    )
    laps = result.scalars().all()
    return [_lap_row(lap) for lap in laps]


@router.get("/{activity_id}/hr_zones")
async def get_hr_zones(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(ActivityMetrics)
        .join(Activity, Activity.id == ActivityMetrics.activity_id)
        .where(ActivityMetrics.activity_id == activity_id, Activity.athlete_id == athlete_id)
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404)

    total_seconds = (m.z1_seconds or 0) + (m.z2_seconds or 0) + (m.z3_seconds or 0) + (m.z4_seconds or 0) + (m.z5_seconds or 0)

    def zone_entry(name, seconds, trimp):
        return {
            "name": name,
            "seconds": seconds or 0,
            "percent": round((seconds or 0) / total_seconds * 100, 1) if total_seconds > 0 else 0,
            "trimp": round(trimp or 0, 2),
        }

    # Zone thresholds must reflect the user's configured max_hr/resting_hr,
    # not the env defaults — otherwise a user with, say, max_hr=195 sees
    # zone boundaries that assume max_hr=190.
    from app.config import get_athlete_settings, get_settings

    athlete = (
        await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    ).scalar_one_or_none()
    s = get_athlete_settings(athlete) if athlete else get_settings()
    hrr = s.max_hr - s.resting_hr
    if s.hr_zone_method == "karvonen":
        zone_thresholds = [round(s.resting_hr + p * hrr) for p in (0.60, 0.70, 0.80, 0.90)]
    else:
        zone_thresholds = [round(s.max_hr * p) for p in (0.60, 0.70, 0.80, 0.90)]

    return {
        "z1": zone_entry("Z1 Recovery", m.z1_seconds, m.trimp_z1),
        "z2": zone_entry("Z2 Aerobic", m.z2_seconds, m.trimp_z2),
        "z3": zone_entry("Z3 Tempo", m.z3_seconds, m.trimp_z3),
        "z4": zone_entry("Z4 Threshold", m.z4_seconds, m.trimp_z4),
        "z5": zone_entry("Z5 VO2max", m.z5_seconds, m.trimp_z5),
        "total_trimp": round(m.trimp_total or 0, 2),
        "total_seconds": total_seconds,
        "zone_thresholds": zone_thresholds,
    }


class RaceFlagRequest(BaseModel):
    is_race: bool


@router.patch("/{activity_id}/race_flag")
async def set_race_flag(
    activity_id: int,
    body: RaceFlagRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Mark/unmark an activity as a race.

    The flag is preserved across Strava refresh/sync cycles — see
    `strava/sync.py` where the upsert path only sets is_race on INSERT,
    not on UPDATE. That way a user-marked race stays marked even if the
    activity is re-fetched with a different name or streams.
    """
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.athlete_id == athlete_id,
        )
    )
    activity = result.scalar_one_or_none()
    if not activity:
        raise HTTPException(status_code=404)

    activity.is_race = 1 if body.is_race else 0
    await db.flush()
    return {"id": activity.id, "is_race": bool(activity.is_race)}


@router.patch("/{activity_id}/laps/{lap_index}")
async def update_lap_correction(
    activity_id: int,
    lap_index: int,
    body: LapCorrectionRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    act_result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    if not act_result.scalar_one_or_none():
        raise HTTPException(status_code=404)

    lap_result = await db.execute(
        select(Lap).where(Lap.activity_id == activity_id, Lap.lap_index == lap_index)
    )
    lap = lap_result.scalar_one_or_none()
    if not lap:
        raise HTTPException(status_code=404, detail="Lap not found")

    lap.corrected_distance = (
        body.corrected_distance_km * 1000.0 if body.corrected_distance_km is not None else None
    )
    lap.corrected_elevation_gain = body.corrected_elevation_gain

    await db.flush()

    from app.analytics.compute_pipeline import apply_treadmill_corrections
    await apply_treadmill_corrections(db, activity_id)

    return _lap_row(lap)


@router.post("/{activity_id}/refresh")
async def refresh_activity_from_strava(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Re-fetch streams, laps, and metadata for a single activity from Strava.
    Preserves any treadmill corrections already applied.
    Recomputes metrics immediately after refresh.
    """
    athlete_id = _get_athlete_id(request)

    act_check = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    if not act_check.scalar_one_or_none():
        raise HTTPException(status_code=404)

    from app.api._errors import translate_strava_error
    from app.strava.sync import refresh_activity

    try:
        success = await refresh_activity(db, activity_id, athlete_id)
    except Exception as e:  # noqa: BLE001
        raise translate_strava_error(e, action="refresh activity") from e

    if not success:
        # The only remaining False path is "activity no longer in our DB".
        raise HTTPException(status_code=404, detail="Activity not found in database")

    # Recompute everything downstream of the refresh so the user sees a
    # fully-populated activity detail page in one click:
    #   (a) per-activity metrics   — zones, TRIMP, cadence, pace, km splits
    #   (b) workout classification  — easy/tempo/threshold/etc.
    # Treadmill corrections are preserved upstream by refresh_activity
    # (activity.treadmill_corrected guard + never writing corrected_* columns).
    import logging

    from app.analytics.classification_engine import classify_workout
    from app.analytics.compute_pipeline import compute_metrics_for_activity
    from app.config import get_athlete_settings

    log = logging.getLogger("racingplanner.refresh")

    athlete = (
        await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    ).scalar_one_or_none()
    # athlete_id filter is redundant with the ownership check above (line 388),
    # but we keep it as defense-in-depth: if a future refactor moves the outer
    # check, this query would silently return another user's activity.
    act_result = await db.execute(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.athlete_id == athlete_id,
        )
    )
    activity = act_result.scalar_one_or_none()

    if activity and activity.streams_synced and athlete:
        # (a) metrics — run without swallowing errors; a DataError here is
        # more useful surfaced (via the global exception handler) than
        # silently leaving the activity half-refreshed.
        await compute_metrics_for_activity(db, activity, get_athlete_settings(athlete))

        # (b) classification — needs the newly-written metrics row plus
        # recent-30 averages for this athlete to decide "long run" etc.
        try:
            metrics_result = await db.execute(
                select(ActivityMetrics).where(ActivityMetrics.activity_id == activity_id)
            )
            metrics_row = metrics_result.scalar_one_or_none()

            # Average over the *last 30* activities — `.limit(30)` on a bare
            # aggregate select is a no-op (a GROUP-BY-less aggregate returns
            # one row), which would average the athlete's entire history.
            # Use a subquery to actually slice the 30 most recent runs first.
            recent_result = await db.execute(
                text("""
                    SELECT AVG(moving_time), AVG(distance)
                    FROM (
                        SELECT moving_time, distance FROM activities
                        WHERE athlete_id = :aid
                          AND moving_time > 0
                          AND distance > 0
                        ORDER BY start_date DESC
                        LIMIT 30
                    ) AS recent30
                """),
                {"aid": athlete_id},
            )
            avg_time, avg_dist = recent_result.one()

            if metrics_row and avg_time and avg_dist:
                workout_type = classify_workout(
                    metrics={
                        "z1_seconds": metrics_row.z1_seconds,
                        "z2_seconds": metrics_row.z2_seconds,
                        "z3_seconds": metrics_row.z3_seconds,
                        "z4_seconds": metrics_row.z4_seconds,
                        "z5_seconds": metrics_row.z5_seconds,
                        "pacing_cv_pct": metrics_row.pacing_cv_pct,
                    },
                    activity={
                        "name": activity.name or "",
                        "moving_time": activity.moving_time,
                        "distance": activity.distance,
                    },
                    recent_avg_duration=float(avg_time),
                    recent_avg_distance=float(avg_dist),
                )
                metrics_row.workout_type = workout_type
        except Exception as e:  # noqa: BLE001
            # Classification failure is non-fatal — the user's metrics are
            # already written; log and move on.
            log.warning("classification failed for %s: %s", activity_id, e)

    await db.commit()

    # Defense-in-depth: include athlete_id on this follow-up fetch too, so
    # this endpoint is self-contained even if the outer ownership check moves.
    result = await db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.id == activity_id,
            Activity.athlete_id == athlete_id,
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404)
    return _activity_row(row[0], row[1])


@router.get("/{activity_id}/similar")
async def get_similar_runs(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Find similar runs (same workout type, distance ±15%) for comparison."""
    athlete_id = _get_athlete_id(request)

    # Load target activity
    result = await db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404)
    target_act, target_met = row
    target_dist = target_act.distance or 0
    if target_dist <= 0:
        return {"reference": _activity_row(target_act, target_met), "similar_runs": []}

    dist_lo = target_dist * 0.85
    dist_hi = target_dist * 1.15
    workout_type = target_met.workout_type if target_met else None

    # Build query — match distance range, optionally filter by workout type
    q = (
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.id != activity_id,
            Activity.distance >= dist_lo,
            Activity.distance <= dist_hi,
        )
    )
    if workout_type:
        q = q.where(ActivityMetrics.workout_type == workout_type)

    q = q.order_by(desc(Activity.start_date)).limit(20)

    sim_result = await db.execute(q)
    similar = sim_result.all()

    similar_rows = []
    for act, met in similar:
        row_data = {
            "id": act.id,
            "name": act.name,
            "date": act.start_date_local[:10] if act.start_date_local else act.start_date[:10],
            "distance_km": round((act.distance or 0) / 1000, 1),
            "moving_time": act.moving_time,
            "avg_pace_sec_per_km": met.avg_pace_sec_per_km,
            "avg_pace_str": _pace_to_str(met.avg_pace_sec_per_km),
            "average_heartrate": act.average_heartrate,
            "ef_first_half": round(met.ef_first_half, 4) if met.ef_first_half else None,
            "pace_decoupling_pct": round(met.pace_decoupling_pct, 1) if met.pace_decoupling_pct else None,
            "pacing_cv_pct": round(met.pacing_cv_pct, 1) if met.pacing_cv_pct else None,
            "workout_type": met.workout_type,
            "rss": round(met.rss, 1) if met.rss else None,
        }
        # Deltas vs target
        if target_met and target_met.avg_pace_sec_per_km and met.avg_pace_sec_per_km:
            row_data["delta_pace"] = round(met.avg_pace_sec_per_km - target_met.avg_pace_sec_per_km, 1)
        if target_act.average_heartrate and act.average_heartrate:
            row_data["delta_hr"] = round(act.average_heartrate - target_act.average_heartrate, 1)
        if target_met and target_met.ef_first_half and met.ef_first_half:
            row_data["delta_ef"] = round(met.ef_first_half - target_met.ef_first_half, 4)
        similar_rows.append(row_data)

    return {
        "reference": _activity_row(target_act, target_met),
        "similar_runs": similar_rows,
    }


@router.get("/{activity_id}/recovery_context")
async def get_recovery_context(
    activity_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Garmin health snapshot from the morning of an activity."""
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    activity = result.scalar_one_or_none()
    if not activity:
        raise HTTPException(status_code=404)

    # Parse local date; if before 6am, look at previous day's sleep data
    local_str = activity.start_date_local or activity.start_date or ""
    target_date = local_str[:10]
    if len(local_str) >= 16:
        try:
            hour = int(local_str[11:13])
            if hour < 6:
                from datetime import date as _date
                from datetime import timedelta
                d = _date.fromisoformat(target_date)
                target_date = (d - timedelta(days=1)).isoformat()
        except (ValueError, IndexError):
            pass

    garmin = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date == target_date,
        )
    )
    g = garmin.scalar_one_or_none()
    if not g:
        return {"available": False}

    return {
        "available": True,
        "date": g.date,
        "body_battery": g.body_battery_latest,
        "hrv_last_night": g.hrv_last_night,
        "hrv_status": g.hrv_status,
        "sleep_hours": round(g.sleep_duration_sec / 3600, 1) if g.sleep_duration_sec else None,
        "sleep_score": g.sleep_score,
        "resting_hr": g.resting_hr,
        "stress_avg": g.stress_avg,
        "training_readiness": g.training_readiness,
    }


@router.post("/sync")
async def sync_new(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    import asyncio
    import logging

    from app.analytics.compute_pipeline import run_full_pipeline

    log = logging.getLogger("racingplanner.sync.incremental")
    q: asyncio.Queue = asyncio.Queue()

    async def _run():
        # Mirror routes_sync.full_sync — surface pipeline errors via the SSE
        # progress queue so the frontend shows them.
        try:
            async with async_session() as session:
                await run_full_pipeline(session, athlete_id, q, full_sync=False)
        except Exception as e:  # noqa: BLE001
            log.exception("Incremental sync pipeline crashed for athlete %s", athlete_id)
            await q.put(f"ERROR: Sync failed: {type(e).__name__}: {e}")
            await q.put("DONE")

    background_tasks.add_task(_run)
    return {"queued": True, "message": "Sync started. Monitor progress at /api/sync/progress"}

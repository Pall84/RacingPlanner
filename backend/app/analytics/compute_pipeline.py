"""
Orchestrates the full sync + metrics computation pipeline.
Emits progress messages to an asyncio.Queue for SSE streaming.
"""
import asyncio
import json

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.analytics.classification_engine import classify_workout, compute_per_activity_vdot
from app.analytics.fitness_engine import (
    compute_vo2max_estimate,
    compute_weekly_summaries,
    rebuild_daily_fitness,
    update_personal_records,
)
from app.analytics.metrics_engine import ActivityMetricsEngine
from app.config import get_athlete_settings, get_settings
from app.models.schema import Activity, ActivityMetrics, ActivityStream, KmSplit, Lap
from app.strava.sync import sync_activities, sync_all_pending_streams


async def compute_metrics_for_activity(db, activity: Activity, settings) -> bool:
    """Load streams, run metrics engine, write results to DB."""
    # Load streams
    result = await db.execute(
        select(ActivityStream).where(ActivityStream.activity_id == activity.id)
    )
    stream_rows = result.scalars().all()
    streams = {row.stream_type: json.loads(row.data_json) for row in stream_rows}

    activity_dict = {
        "id": activity.id,
        "distance": activity.distance,
        "moving_time": activity.moving_time,
        "elapsed_time": activity.elapsed_time,
        "average_heartrate": activity.average_heartrate,
        "max_heartrate": activity.max_heartrate,
    }

    engine = ActivityMetricsEngine(
        activity=activity_dict,
        streams=streams,
        max_hr=settings.max_hr,
        resting_hr=settings.resting_hr,
        ftp_watts=settings.ftp_watts,
        hr_zone_method=settings.hr_zone_method,
        trimp_gender=settings.trimp_gender,
    )
    m = engine.compute_all()

    # Compute per-activity VDOT — but only for hard efforts.
    # VDOT is defined for near-maximal performances (Daniels' original tables
    # assume a race or time trial). Computing it from an easy run tells you
    # "what VO2max would be required to run this pace maximally" — not the
    # athlete's VO2max. Applying it indiscriminately pollutes the trends
    # scatter plot with bogus low numbers. Gate on average HR ≥ 85% of max,
    # which captures races, threshold, and VO2max intervals. No HR → no VDOT.
    estimated_vdot = None
    hr_gate = 0.85 * settings.max_hr if settings.max_hr else None
    if (
        hr_gate
        and activity.average_heartrate
        and activity.average_heartrate >= hr_gate
    ):
        estimated_vdot = compute_per_activity_vdot(activity.distance, activity.moving_time)

    # Upsert activity_metrics
    await db.execute(
        pg_insert(ActivityMetrics).values(
            activity_id=activity.id,
            avg_pace_sec_per_km=m.avg_pace_sec_per_km,
            best_pace_sec_per_km=m.best_pace_sec_per_km,
            avg_gap_sec_per_km=m.avg_gap_sec_per_km,
            ef_first_half=m.ef_first_half,
            ef_second_half=m.ef_second_half,
            pace_decoupling_pct=m.pace_decoupling_pct,
            cadence_avg=m.cadence_avg,
            cadence_min=m.cadence_min,
            cadence_max=m.cadence_max,
            cadence_cv_pct=m.cadence_cv_pct,
            stride_length_avg_m=m.stride_length_avg_m,
            stride_length_cv_pct=m.stride_length_cv_pct,
            z1_seconds=m.z1_seconds,
            z2_seconds=m.z2_seconds,
            z3_seconds=m.z3_seconds,
            z4_seconds=m.z4_seconds,
            z5_seconds=m.z5_seconds,
            trimp_total=m.trimp_total,
            trimp_z1=m.trimp_z1,
            trimp_z2=m.trimp_z2,
            trimp_z3=m.trimp_z3,
            trimp_z4=m.trimp_z4,
            trimp_z5=m.trimp_z5,
            rss=m.rss,
            normalized_power=m.normalized_power,
            intensity_factor=m.intensity_factor,
            pacing_cv_pct=m.pacing_cv_pct,
            moving_elapsed_ratio=m.moving_elapsed_ratio,
            total_elevation_loss=m.total_elevation_loss,
            estimated_vdot=estimated_vdot,
        ).on_conflict_do_update(
            index_elements=["activity_id"],
            set_={
                "avg_pace_sec_per_km": m.avg_pace_sec_per_km,
                "best_pace_sec_per_km": m.best_pace_sec_per_km,
                "avg_gap_sec_per_km": m.avg_gap_sec_per_km,
                "ef_first_half": m.ef_first_half,
                "ef_second_half": m.ef_second_half,
                "pace_decoupling_pct": m.pace_decoupling_pct,
                "cadence_avg": m.cadence_avg,
                "cadence_min": m.cadence_min,
                "cadence_max": m.cadence_max,
                "cadence_cv_pct": m.cadence_cv_pct,
                "stride_length_avg_m": m.stride_length_avg_m,
                "stride_length_cv_pct": m.stride_length_cv_pct,
                "z1_seconds": m.z1_seconds,
                "z2_seconds": m.z2_seconds,
                "z3_seconds": m.z3_seconds,
                "z4_seconds": m.z4_seconds,
                "z5_seconds": m.z5_seconds,
                "trimp_total": m.trimp_total,
                "trimp_z1": m.trimp_z1,
                "trimp_z2": m.trimp_z2,
                "trimp_z3": m.trimp_z3,
                "trimp_z4": m.trimp_z4,
                "trimp_z5": m.trimp_z5,
                "rss": m.rss,
                "normalized_power": m.normalized_power,
                "intensity_factor": m.intensity_factor,
                "pacing_cv_pct": m.pacing_cv_pct,
                "moving_elapsed_ratio": m.moving_elapsed_ratio,
                "total_elevation_loss": m.total_elevation_loss,
                "estimated_vdot": estimated_vdot,
            },
        )
    )

    # Delete old km splits and re-insert
    from sqlalchemy import delete
    await db.execute(delete(KmSplit).where(KmSplit.activity_id == activity.id))
    for split in m.km_splits:
        await db.execute(
            pg_insert(KmSplit).values(
                activity_id=activity.id,
                km_index=split["km_index"],
                distance_m=split["distance_m"],
                duration_sec=split["duration_sec"],
                pace_sec_per_km=split["pace_sec_per_km"],
                gap_sec_per_km=split["gap_sec_per_km"],
                avg_hr=split["avg_hr"],
                avg_cadence=split["avg_cadence"],
                elevation_gain=split["elevation_gain"],
                elevation_loss=split["elevation_loss"],
                avg_grade_pct=split["avg_grade_pct"],
            )
        )

    # Mark computed
    activity.metrics_computed = 1
    await db.flush()
    return True


async def apply_treadmill_corrections(db, activity_id: int):
    """
    Recalculate activity totals and lap paces from user-supplied lap corrections.

    For each lap, the effective distance/elevation is:
      - corrected value if set by the user
      - original Strava value otherwise

    Updates:
      - lap.pace_sec_per_km for every lap (from effective distance + moving_time)
      - activity.distance / total_elevation_gain / average_speed (sum of effective lap values)
      - activity.treadmill_corrected = 1
      - activity.metrics_computed = 0  (so next pipeline run refreshes stream-based metrics)
    """
    lap_result = await db.execute(
        select(Lap).where(Lap.activity_id == activity_id).order_by(Lap.lap_index)
    )
    laps = lap_result.scalars().all()

    act_result = await db.execute(
        select(Activity).where(Activity.id == activity_id)
    )
    activity = act_result.scalar_one_or_none()
    if not activity:
        return

    total_distance = 0.0
    total_elevation = 0.0

    for lap in laps:
        eff_dist = lap.corrected_distance if lap.corrected_distance is not None else (lap.distance or 0.0)
        eff_elev = lap.corrected_elevation_gain if lap.corrected_elevation_gain is not None else (lap.total_elevation_gain or 0.0)
        total_distance += eff_dist
        total_elevation += eff_elev
        if lap.moving_time and lap.moving_time > 0 and eff_dist > 0:
            lap.pace_sec_per_km = lap.moving_time / (eff_dist / 1000.0)

    activity.distance = total_distance
    activity.total_elevation_gain = total_elevation
    if activity.moving_time and activity.moving_time > 0 and total_distance > 0:
        activity.average_speed = total_distance / activity.moving_time
    activity.treadmill_corrected = 1
    activity.metrics_computed = 0

    await db.flush()


async def run_full_pipeline(
    db,
    athlete_id: int,
    progress_queue: asyncio.Queue,
    full_sync: bool = False,
):
    """Run the complete sync + analysis pipeline."""
    # Load athlete to get per-athlete DB settings (overrides .env defaults)
    from app.models.schema import Athlete
    athlete_result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete_row = athlete_result.scalar_one_or_none()
    settings = get_athlete_settings(athlete_row) if athlete_row else get_settings()

    async def emit(msg: str):
        await progress_queue.put(msg)

    await emit("Step 1/9: Syncing activities from Strava...")
    new_count = await sync_activities(db, athlete_id, full_sync=full_sync, progress_queue=progress_queue)
    await emit(f"Synced {new_count} new activities.")

    await emit("Step 2/9: Downloading activity streams...")
    await sync_all_pending_streams(db, athlete_id, progress_queue=progress_queue)

    await emit("Step 3/9: Computing per-activity metrics...")
    result = await db.execute(
        select(Activity).where(
            Activity.athlete_id == athlete_id,
            Activity.metrics_computed == 0,
            Activity.streams_synced == 1,
        ).order_by(Activity.start_date.desc())
    )
    pending = result.scalars().all()
    total = len(pending)

    for i, activity in enumerate(pending):
        try:
            await compute_metrics_for_activity(db, activity, settings)
        except Exception as e:
            await emit(f"  Warning: metrics failed for {activity.id}: {e}")
        if i % 5 == 0:
            await emit(f"  Metrics: {i+1}/{total}")
            await db.commit()

    await db.commit()

    await emit("Step 4/9: Classifying workouts...")
    # Compute recent averages for classification context (last 30 activities)
    avg_result = await db.execute(
        text("""
            SELECT AVG(moving_time), AVG(distance)
            FROM (
                SELECT moving_time, distance FROM activities
                WHERE athlete_id = :aid AND moving_time > 0 AND distance > 0
                ORDER BY start_date DESC LIMIT 30
            ) AS recent30
        """),
        {"aid": athlete_id},
    )
    avg_row = avg_result.one()
    recent_avg_duration = avg_row[0] or 0
    recent_avg_distance = avg_row[1] or 0

    # Find activities needing classification
    unclassified = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.metrics_computed == 1,
            ActivityMetrics.workout_type.is_(None),
        )
    )
    classify_count = 0
    for activity, metrics in unclassified.all():
        metrics_dict = {
            "z1_seconds": metrics.z1_seconds,
            "z2_seconds": metrics.z2_seconds,
            "z3_seconds": metrics.z3_seconds,
            "z4_seconds": metrics.z4_seconds,
            "z5_seconds": metrics.z5_seconds,
            "pacing_cv_pct": metrics.pacing_cv_pct,
        }
        activity_dict = {
            "name": activity.name,
            "moving_time": activity.moving_time,
            "distance": activity.distance,
        }
        metrics.workout_type = classify_workout(
            metrics_dict, activity_dict, recent_avg_duration, recent_avg_distance
        )
        classify_count += 1

    await db.commit()
    if classify_count:
        await emit(f"  Classified {classify_count} activities.")

    await emit("Step 5/9: Rebuilding fitness/form (CTL/ATL/TSB)...")
    await rebuild_daily_fitness(db, athlete_id)
    await db.commit()

    await emit("Step 6/9: Computing weekly summaries...")
    await compute_weekly_summaries(db, athlete_id)
    await db.commit()

    await emit("Step 7/9: Updating personal records...")
    await update_personal_records(db, athlete_id)
    await db.commit()

    await emit("Step 8/9: Estimating VO2max...")
    vo2max = await compute_vo2max_estimate(db, athlete_id, max_hr=settings.max_hr)
    if vo2max:
        await emit(f"Estimated VO2max: {vo2max:.1f} mL/kg/min")

    await emit("Step 9/9: Syncing Garmin health data...")
    try:
        from app.garmin.sync import sync_garmin_health
        garmin_count = await sync_garmin_health(
            db, athlete_id, days=14, progress_queue=progress_queue,
        )
        if garmin_count > 0:
            await emit(f"  Synced {garmin_count} days of Garmin health data.")
        else:
            await emit("  Garmin not connected or no new data.")
    except Exception as e:
        await emit(f"  Garmin sync skipped: {e}")

    await emit("DONE")

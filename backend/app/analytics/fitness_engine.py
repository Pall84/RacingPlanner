"""
Multi-activity fitness engine.
Computes CTL/ATL/TSB, weekly summaries, VO2max estimates, personal records.
"""
import math
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select, text

from app.models.schema import (
    Activity,
    ActivityMetrics,
    GarminDailyHealth,
)


def _iso_to_date(dt_str: str) -> date:
    return date.fromisoformat(dt_str[:10])


def _week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def rebuild_daily_fitness(db, athlete_id: int):
    """Rebuild the entire CTL/ATL/TSB time series from scratch."""
    result = await db.execute(
        select(
            Activity.start_date,
            ActivityMetrics.rss,
        )
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_date)
    )
    rows = result.all()
    if not rows:
        return

    # Aggregate RSS per date
    rss_by_date: dict[date, float] = defaultdict(float)
    for start_date_str, rss in rows:
        d = _iso_to_date(start_date_str)
        rss_by_date[d] += rss or 0.0

    first_day = min(rss_by_date.keys())
    today = date.today()

    k_ctl = 1 - math.exp(-1 / 42)
    k_atl = 1 - math.exp(-1 / 7)

    ctl = 0.0
    atl = 0.0

    current = first_day
    while current <= today:
        daily_rss = rss_by_date.get(current, 0.0)
        # TSB uses previous day's CTL and ATL
        tsb = ctl - atl
        # Update
        ctl = ctl + k_ctl * (daily_rss - ctl)
        atl = atl + k_atl * (daily_rss - atl)

        await db.execute(
            # Composite PK on (date, athlete_id) — must be listed in conflict
            # target. Postgres upsert syntax: EXCLUDED refers to the row that
            # would have been inserted. `extract(epoch from now())` is the
            # Postgres equivalent of SQLite's `strftime('%s','now')`.
            text("""
                INSERT INTO daily_fitness (date, athlete_id, daily_rss, ctl, atl, tsb)
                VALUES (:date, :athlete_id, :daily_rss, :ctl, :atl, :tsb)
                ON CONFLICT(date, athlete_id) DO UPDATE SET
                    daily_rss = excluded.daily_rss,
                    ctl = excluded.ctl,
                    atl = excluded.atl,
                    tsb = excluded.tsb,
                    updated_at = extract(epoch from now())::bigint
            """),
            {
                "date": current.isoformat(),
                "athlete_id": athlete_id,
                "daily_rss": daily_rss,
                "ctl": ctl,
                "atl": atl,
                "tsb": tsb,
            },
        )
        current += timedelta(days=1)

    await db.flush()


async def compute_weekly_summaries(db, athlete_id: int):
    """Compute per-week training summaries including monotony and strain."""
    result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_date)
    )
    rows = result.all()

    weeks: dict[date, dict] = defaultdict(lambda: {
        "activities": [],
        "distances": [],
        "times": [],
        "elevations": [],
        "rss_list": [],
        "ef_list": [],
    })

    for activity, metrics in rows:
        d = _iso_to_date(activity.start_date)
        week_key = _week_monday(d)
        w = weeks[week_key]
        w["activities"].append(activity)
        w["distances"].append(activity.distance or 0)
        w["times"].append(activity.moving_time or 0)
        w["elevations"].append(activity.total_elevation_gain or 0)
        w["rss_list"].append(metrics.rss or 0)
        if metrics.ef_first_half:
            w["ef_list"].append(metrics.ef_first_half)

    for week_start, data in weeks.items():
        run_count = len(data["activities"])
        total_distance = sum(data["distances"])
        total_time = sum(data["times"])
        total_elevation = sum(data["elevations"])
        total_rss = sum(data["rss_list"])

        # Training monotony: mean daily load / std daily load
        # Build 7-day daily RSS for the week
        daily_rss_in_week = defaultdict(float)
        for act, rss in zip(data["activities"], data["rss_list"]):
            d = _iso_to_date(act.start_date)
            daily_rss_in_week[d] += rss
        daily_values = [daily_rss_in_week.get(week_start + timedelta(days=i), 0.0) for i in range(7)]
        mean_daily = sum(daily_values) / 7
        if mean_daily > 0:
            variance = sum((v - mean_daily) ** 2 for v in daily_values) / 7
            std_daily = variance ** 0.5
            monotony = mean_daily / std_daily if std_daily > 0 else 0.0
        else:
            monotony = 0.0
        strain = monotony * total_rss

        avg_ef = sum(data["ef_list"]) / len(data["ef_list"]) if data["ef_list"] else None

        # Get average CTL for this week (from daily_fitness)
        week_end = week_start + timedelta(days=6)
        avg_ctl_result = await db.execute(
            text("""
                SELECT AVG(ctl) FROM daily_fitness
                WHERE athlete_id = :aid AND date >= :start AND date <= :end
            """),
            {"aid": athlete_id, "start": week_start.isoformat(), "end": week_end.isoformat()},
        )
        avg_ctl = avg_ctl_result.scalar()

        await db.execute(
            text("""
                INSERT INTO weekly_summary
                    (week_start, athlete_id, run_count, total_distance, total_time,
                     total_elevation, total_rss, avg_ctl, training_monotony, training_strain, avg_ef)
                VALUES
                    (:ws, :aid, :rc, :dist, :time, :elev, :rss, :ctl, :mono, :strain, :ef)
                ON CONFLICT(week_start, athlete_id) DO UPDATE SET
                    run_count = excluded.run_count,
                    total_distance = excluded.total_distance,
                    total_time = excluded.total_time,
                    total_elevation = excluded.total_elevation,
                    total_rss = excluded.total_rss,
                    avg_ctl = excluded.avg_ctl,
                    training_monotony = excluded.training_monotony,
                    training_strain = excluded.training_strain,
                    avg_ef = excluded.avg_ef
            """),
            {
                "ws": week_start.isoformat(),
                "aid": athlete_id,
                "rc": run_count,
                "dist": total_distance,
                "time": total_time,
                "elev": total_elevation,
                "rss": total_rss,
                "ctl": avg_ctl,
                "mono": monotony,
                "strain": strain,
                "ef": avg_ef,
            },
        )

    await db.flush()


def _jack_daniels_vdot(speed_m_per_min: float, duration_min: float) -> float:
    """
    Jack Daniels VDOT estimate from a performance.
    speed: meters per minute
    duration: minutes
    """
    v = speed_m_per_min
    t = duration_min
    if t <= 0 or v <= 0:
        return 0.0
    numerator = -4.6 + 0.182258 * v + 0.000104 * v**2
    denominator = (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t)
        + 0.2989558 * math.exp(-0.1932605 * t)
    )
    if denominator == 0:
        return 0.0
    return numerator / denominator


async def compute_vo2max_estimate(db, athlete_id: int, max_hr: int = 190) -> float | None:
    """
    Estimate VO2max from best aerobic threshold efforts (HR 85-95% max).
    Uses activities from the last 180 days with distance > 5km.
    """
    threshold_lo = 0.85 * max_hr
    threshold_hi = 0.95 * max_hr

    result = await db.execute(
        select(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.distance >= 5000,
            Activity.average_heartrate >= threshold_lo,
            Activity.average_heartrate <= threshold_hi,
            Activity.moving_time > 0,
        )
        .order_by(Activity.start_date.desc())
        .limit(20)
    )
    candidates = result.scalars().all()
    if not candidates:
        # Fallback: use all runs with HR data, take the best (fastest relative to HR)
        result = await db.execute(
            select(Activity)
            .where(
                Activity.athlete_id == athlete_id,
                Activity.distance >= 5000,
                Activity.average_heartrate > 0,
                Activity.moving_time > 0,
            )
            .order_by(Activity.start_date.desc())
            .limit(50)
        )
        candidates = result.scalars().all()

    if not candidates:
        return None

    best_vdot = 0.0
    for act in candidates:
        speed_m_per_min = (act.distance / act.moving_time) * 60.0
        duration_min = act.moving_time / 60.0
        vdot = _jack_daniels_vdot(speed_m_per_min, duration_min)
        if vdot > best_vdot:
            best_vdot = vdot

    return best_vdot if best_vdot > 0 else None


async def compute_blended_readiness(
    db, athlete_id: int, tsb: float, acwr: float | None,
) -> dict:
    """
    Compute readiness score, blending TSB-based score with Garmin
    recovery data when available.

    Returns dict with: score, label, sources, and optional garmin_factors.
    """
    # Base: TSB readiness
    base_score = max(0, min(100, int(50 + tsb * 2)))
    if acwr is not None and acwr > 1.3:
        base_score = max(0, base_score - 15)

    # Try to find recent Garmin data (today or yesterday)
    from sqlalchemy import desc, select
    result = await db.execute(
        select(GarminDailyHealth)
        .where(GarminDailyHealth.athlete_id == athlete_id)
        .order_by(desc(GarminDailyHealth.date))
        .limit(1)
    )
    garmin = result.scalar_one_or_none()

    # Check data is recent (within 2 days)
    garmin_factors = None
    if garmin and garmin.training_readiness is not None:
        from datetime import date as _date
        try:
            data_date = _date.fromisoformat(garmin.date)
            days_old = (_date.today() - data_date).days
        except Exception:
            days_old = 999

        if days_old <= 2:
            garmin_readiness = garmin.training_readiness
            # Normalize sleep: 7-9h = 80-100, <5h = 30, linear between
            sleep_norm = 50.0
            if garmin.sleep_duration_sec:
                hours = garmin.sleep_duration_sec / 3600
                sleep_norm = max(0, min(100, (hours - 4) / 5 * 100))
            battery = garmin.body_battery_latest or 50

            blended = int(
                0.4 * base_score
                + 0.3 * garmin_readiness
                + 0.15 * sleep_norm
                + 0.15 * battery
            )
            blended = max(0, min(100, blended))

            garmin_factors = {
                "training_readiness": garmin_readiness,
                "body_battery": garmin.body_battery_latest,
                "sleep_hours": round(garmin.sleep_duration_sec / 3600, 1) if garmin.sleep_duration_sec else None,
                "sleep_score": garmin.sleep_score,
                "hrv_last_night": garmin.hrv_last_night,
                "hrv_status": garmin.hrv_status,
                "resting_hr": garmin.resting_hr,
                "stress_avg": garmin.stress_avg,
            }

            score = blended
            sources = ["tsb", "garmin"]
        else:
            score = base_score
            sources = ["tsb"]
    else:
        score = base_score
        sources = ["tsb"]

    if score >= 80:
        label = "Fresh"
    elif score >= 60:
        label = "Ready"
    elif score >= 40:
        label = "Tired"
    else:
        label = "Fatigued"

    out = {"score": score, "label": label, "sources": sources}
    if garmin_factors:
        out["garmin_factors"] = garmin_factors
    return out


_DISTANCE_RANGES = {
    "best_5k": (4500, 5500),
    "best_10k": (9200, 10800),
    "best_half_marathon": (20000, 22500),
    "best_marathon": (41000, 43500),
}


async def update_personal_records(db, athlete_id: int):
    """Scan all run activities and update personal records table."""
    result = await db.execute(
        select(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.moving_time > 0,
            Activity.distance > 0,
        )
        .order_by(Activity.start_date)
    )
    activities = result.scalars().all()

    records: dict[str, dict] = {}

    for act in activities:
        dist = act.distance or 0
        time = act.moving_time or 0
        date_str = act.start_date[:10]
        speed = dist / time if time > 0 else 0

        # Longest run
        if "longest_run" not in records or dist > records["longest_run"]["value"]:
            records["longest_run"] = {"value": dist, "date": date_str, "activity_id": act.id}

        # Most elevation
        elev = act.total_elevation_gain or 0
        if "most_elevation" not in records or elev > records["most_elevation"]["value"]:
            records["most_elevation"] = {"value": elev, "date": date_str, "activity_id": act.id}

        # Fastest pace (best speed over any run > 1km)
        if dist > 1000 and (
            "fastest_pace" not in records or speed > records["fastest_pace"]["value"]
        ):
            records["fastest_pace"] = {"value": speed, "date": date_str, "activity_id": act.id}

        # Distance-specific records
        for rec_type, (lo, hi) in _DISTANCE_RANGES.items():
            if lo <= dist <= hi:
                if rec_type not in records or time < records[rec_type]["value"]:
                    records[rec_type] = {"value": time, "date": date_str, "activity_id": act.id}

    for rec_type, data in records.items():
        await db.execute(
            text("""
                INSERT INTO personal_records (athlete_id, record_type, activity_id, value, date)
                VALUES (:aid, :rtype, :actid, :val, :date)
                ON CONFLICT(athlete_id, record_type) DO UPDATE SET
                    activity_id = excluded.activity_id,
                    value = excluded.value,
                    date = excluded.date
            """),
            {
                "aid": athlete_id,
                "rtype": rec_type,
                "actid": data["activity_id"],
                "val": data["value"],
                "date": data["date"],
            },
        )

    await db.flush()

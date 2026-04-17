from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.fitness_engine import compute_blended_readiness, compute_vo2max_estimate
from app.api.deps import get_db
from app.models.schema import (
    Activity,
    ActivityMetrics,
    DailyFitness,
    GarminDailyHealth,
    PersonalRecord,
    WeeklySummary,
)
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/fitness", tags=["fitness"])


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


@router.get("/ctl_atl_tsb")
async def get_ctl_atl_tsb(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    if not start:
        start = (date.today() - timedelta(days=365)).isoformat()
    if not end:
        end = date.today().isoformat()

    result = await db.execute(
        select(DailyFitness)
        .where(
            DailyFitness.athlete_id == athlete_id,
            DailyFitness.date >= start,
            DailyFitness.date <= end,
        )
        .order_by(DailyFitness.date)
    )
    rows = result.scalars().all()
    return [
        {
            "date": r.date,
            "ctl": round(r.ctl or 0, 1),
            "atl": round(r.atl or 0, 1),
            "tsb": round(r.tsb or 0, 1),
            "daily_rss": round(r.daily_rss or 0, 1),
        }
        for r in rows
    ]


@router.get("/weekly")
async def get_weekly(
    request: Request,
    weeks: int = 52,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(weeks=weeks)).isoformat()

    result = await db.execute(
        select(WeeklySummary)
        .where(
            WeeklySummary.athlete_id == athlete_id,
            WeeklySummary.week_start >= start,
        )
        .order_by(WeeklySummary.week_start)
    )
    rows = result.scalars().all()

    # Batch-fetch Garmin weekly averages.
    # Postgres equivalent of SQLite's `date(x, 'weekday 0', '-6 days')`:
    # date_trunc('week', ...) returns the Monday 00:00 of that week.
    garmin_result = await db.execute(
        text("""
            SELECT
                to_char(date_trunc('week', (garmin_daily_health.date)::date), 'YYYY-MM-DD') as week_start,
                AVG(CASE WHEN sleep_duration_sec IS NOT NULL THEN sleep_duration_sec / 3600.0 END) as avg_sleep,
                AVG(resting_hr) as avg_rhr
            FROM garmin_daily_health
            WHERE athlete_id = :aid AND date >= :start
            GROUP BY week_start
        """),
        {"aid": athlete_id, "start": start},
    )
    garmin_weekly = {
        r[0]: {
            "avg_sleep_hours": round(r[1], 1) if r[1] else None,
            "avg_resting_hr": round(r[2], 0) if r[2] else None,
        }
        for r in garmin_result.fetchall()
    }

    return [
        {
            "week_start": r.week_start,
            "run_count": r.run_count,
            "total_distance_km": round((r.total_distance or 0) / 1000, 1),
            "total_time_min": round((r.total_time or 0) / 60, 0),
            "total_elevation": round(r.total_elevation or 0, 0),
            "total_rss": round(r.total_rss or 0, 1),
            "avg_ctl": round(r.avg_ctl or 0, 1),
            "training_monotony": round(r.training_monotony or 0, 2),
            "training_strain": round(r.training_strain or 0, 1),
            "avg_ef": r.avg_ef,
            "avg_sleep_hours": garmin_weekly.get(r.week_start, {}).get("avg_sleep_hours"),
            "avg_resting_hr": garmin_weekly.get(r.week_start, {}).get("avg_resting_hr"),
        }
        for r in rows
    ]


@router.get("/personal_records")
async def get_personal_records(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(PersonalRecord).where(PersonalRecord.athlete_id == athlete_id)
    )
    records = result.scalars().all()

    def _fmt(rec_type: str, value: float) -> str:
        if rec_type == "longest_run":
            return f"{value/1000:.1f} km"
        if rec_type == "most_elevation":
            return f"{value:.0f} m"
        if rec_type == "fastest_pace":
            # value is m/s
            pace_sec = 1000 / value if value > 0 else 0
            return f"{int(pace_sec//60)}:{int(pace_sec%60):02d} /km"
        # Distance PRs: value is seconds (moving_time)
        h = int(value // 3600)
        m = int((value % 3600) // 60)
        s = int(value % 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    return {
        r.record_type: {
            "value": r.value,
            "formatted": _fmt(r.record_type, r.value),
            "date": r.date,
            "activity_id": r.activity_id,
        }
        for r in records
    }


@router.get("/aerobic_efficiency")
async def get_aerobic_efficiency(
    request: Request,
    weeks: int = 26,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(weeks=weeks)).isoformat()

    result = await db.execute(
        select(WeeklySummary)
        .where(
            WeeklySummary.athlete_id == athlete_id,
            WeeklySummary.week_start >= start,
            WeeklySummary.avg_ef.isnot(None),
        )
        .order_by(WeeklySummary.week_start)
    )
    rows = result.scalars().all()
    return [
        {"week_start": r.week_start, "avg_ef": r.avg_ef}
        for r in rows
    ]


@router.get("/vo2max")
async def get_vo2max(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.config import get_settings
    athlete_id = _get_athlete_id(request)
    settings = get_settings()
    vo2max = await compute_vo2max_estimate(db, athlete_id, max_hr=settings.max_hr)
    return {"estimated_vo2max": round(vo2max, 1) if vo2max else None}


@router.get("/summary")
async def get_summary(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.config import get_settings

    athlete_id = _get_athlete_id(request)
    settings = get_settings()

    # Current CTL/ATL/TSB (latest)
    fitness_result = await db.execute(
        select(DailyFitness)
        .where(DailyFitness.athlete_id == athlete_id)
        .order_by(desc(DailyFitness.date))
        .limit(1)
    )
    fitness = fitness_result.scalar_one_or_none()

    # YTD stats
    ytd_start = f"{date.today().year}-01-01"
    ytd_result = await db.execute(
        select(
            func.count(Activity.id).label("run_count"),
            func.sum(Activity.distance).label("total_distance"),
        ).where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= ytd_start,
        )
    )
    ytd = ytd_result.one()

    # VO2max
    vo2max = await compute_vo2max_estimate(db, athlete_id, max_hr=settings.max_hr)

    # Recent 5 activities
    recent_result = await db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.athlete_id == athlete_id)
        .order_by(desc(Activity.start_date))
        .limit(5)
    )
    recent = recent_result.all()

    def _pace_str(sec):
        if not sec:
            return None
        return f"{int(sec//60)}:{int(sec%60):02d}"

    # ACWR — Acute:Chronic Workload Ratio
    ctl = round(fitness.ctl, 1) if fitness and fitness.ctl else 0
    atl = round(fitness.atl, 1) if fitness and fitness.atl else 0
    tsb = round(fitness.tsb, 1) if fitness and fitness.tsb else 0
    acwr = round(atl / ctl, 2) if ctl > 0 else None
    if acwr is not None:
        acwr_status = "danger" if acwr > 1.5 else ("caution" if acwr > 1.3 else "ok")
    else:
        acwr_status = "ok"

    # Readiness score (0-100) — blended with Garmin data when available
    readiness_data = await compute_blended_readiness(db, athlete_id, tsb, acwr)
    readiness_score = readiness_data["score"]
    readiness_label = readiness_data["label"]

    # Check Garmin connection status
    from app.models.schema import GarminCredentials, GarminDailyHealth
    garmin_cred = await db.execute(
        select(GarminCredentials).where(GarminCredentials.athlete_id == athlete_id)
    )
    garmin_connected = garmin_cred.scalar_one_or_none() is not None

    garmin_latest = None
    if garmin_connected:
        garmin_result = await db.execute(
            select(GarminDailyHealth)
            .where(GarminDailyHealth.athlete_id == athlete_id)
            .order_by(desc(GarminDailyHealth.date))
            .limit(1)
        )
        g = garmin_result.scalar_one_or_none()
        if g:
            garmin_latest = {
                "date": g.date,
                "hrv_last_night": g.hrv_last_night,
                "hrv_status": g.hrv_status,
                "resting_hr": g.resting_hr,
                "body_battery_latest": g.body_battery_latest,
                "sleep_hours": round(g.sleep_duration_sec / 3600, 1) if g.sleep_duration_sec else None,
                "sleep_score": g.sleep_score,
                "stress_avg": g.stress_avg,
                "training_readiness": g.training_readiness,
                "vo2max_running": g.vo2max_running,
            }

    # Recommended weekly load (5-10% progressive overload on last 4 weeks avg)
    today_d = date.today()
    four_weeks_ago = (today_d - timedelta(weeks=4)).isoformat()
    this_monday = (today_d - timedelta(days=today_d.weekday())).isoformat()
    load_result = await db.execute(
        text("""
            SELECT AVG(total_rss) FROM weekly_summary
            WHERE athlete_id = :aid AND week_start >= :start AND week_start < :this_monday
        """),
        {"aid": athlete_id, "start": four_weeks_ago, "this_monday": this_monday},
    )
    avg_4wk_rss = load_result.scalar() or 0

    # Current week RSS
    week_rss_result = await db.execute(
        text("""
            SELECT COALESCE(SUM(daily_rss), 0) FROM daily_fitness
            WHERE athlete_id = :aid AND date >= :monday AND date <= :today
        """),
        {"aid": athlete_id, "monday": this_monday, "today": today_d.isoformat()},
    )
    current_week_rss = round(week_rss_result.scalar() or 0, 1)

    return {
        "current_ctl": ctl,
        "current_atl": atl,
        "current_tsb": tsb,
        "acwr": acwr,
        "acwr_status": acwr_status,
        "readiness_score": readiness_score,
        "readiness_label": readiness_label,
        "readiness_sources": readiness_data["sources"],
        "garmin_connected": garmin_connected,
        "garmin_latest": garmin_latest,
        "recommended_load": {
            "min_rss": round(avg_4wk_rss * 1.05, 1),
            "max_rss": round(avg_4wk_rss * 1.10, 1),
            "last_4wk_avg_rss": round(avg_4wk_rss, 1),
        },
        "current_week_rss": current_week_rss,
        "ytd_runs": ytd.run_count or 0,
        "ytd_distance_km": round((ytd.total_distance or 0) / 1000, 1),
        "estimated_vo2max": round(vo2max, 1) if vo2max else None,
        "recent_activities": [
            {
                "id": act.id,
                "name": act.name,
                "date": act.start_date_local[:10] if act.start_date_local else None,
                "distance_km": round((act.distance or 0) / 1000, 1),
                "moving_time": act.moving_time,
                "avg_hr": act.average_heartrate,
                "avg_pace_str": _pace_str(met.avg_pace_sec_per_km if met else None),
                "rss": round(met.rss, 1) if met and met.rss else None,
                "workout_type": met.workout_type if met else None,
            }
            for act, met in recent
        ],
    }


@router.get("/training_distribution")
async def get_training_distribution(
    request: Request,
    weeks: int = 12,
    db: AsyncSession = Depends(get_db),
):
    """Workout type distribution over the last N weeks."""
    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(weeks=weeks)).isoformat()

    result = await db.execute(
        select(
            ActivityMetrics.workout_type,
            func.count().label("cnt"),
            func.sum(Activity.moving_time).label("total_time"),
        )
        .join(Activity, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= start,
            ActivityMetrics.workout_type.isnot(None),
        )
        .group_by(ActivityMetrics.workout_type)
    )
    rows = result.all()

    total_count = sum(r.cnt for r in rows)
    total_time = sum(r.total_time or 0 for r in rows)

    distribution = []
    for r in rows:
        distribution.append({
            "type": r.workout_type,
            "count": r.cnt,
            "total_time_min": round((r.total_time or 0) / 60, 0),
            "pct_count": round(r.cnt / total_count * 100, 1) if total_count else 0,
            "pct_time": round((r.total_time or 0) / total_time * 100, 1) if total_time else 0,
        })

    # Sort by count descending
    distribution.sort(key=lambda x: x["count"], reverse=True)

    return {
        "distribution": distribution,
        "total_count": total_count,
        "total_time_min": round(total_time / 60, 0) if total_time else 0,
        "weeks": weeks,
    }


@router.get("/performance_trends")
async def get_performance_trends(
    request: Request,
    weeks: int = 52,
    db: AsyncSession = Depends(get_db),
):
    """
    Four performance trend series in one endpoint:
    - vo2max: per-week max estimated_vdot
    - pace_decoupling: weekly avg for easy/moderate/long_run
    - pacing_cv: weekly avg pacing consistency
    - ef_per_activity: per-activity EF data points
    """
    from collections import defaultdict

    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(weeks=weeks)).isoformat()

    result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= start,
        )
        .order_by(Activity.start_date)
    )
    rows = result.all()

    # Aggregate by week
    def _week_monday(d_str):
        from datetime import date as _date
        d = _date.fromisoformat(d_str[:10])
        return (d - timedelta(days=d.weekday())).isoformat()

    vo2max_by_week = defaultdict(list)
    decouple_by_week = defaultdict(list)
    pacing_by_week = defaultdict(list)
    ef_points = []

    for act, met in rows:
        wk = _week_monday(act.start_date)

        if met.estimated_vdot and met.estimated_vdot > 0:
            vo2max_by_week[wk].append(met.estimated_vdot)

        # Pace decoupling — only for easy/moderate/long_run (intervals/races skew this)
        if met.pace_decoupling_pct is not None and met.workout_type in ("easy", "moderate", "long_run", None):
            decouple_by_week[wk].append(met.pace_decoupling_pct)

        if met.pacing_cv_pct is not None:
            pacing_by_week[wk].append(met.pacing_cv_pct)

        if met.ef_first_half is not None:
            ef_points.append({
                "date": act.start_date[:10],
                "ef": round(met.ef_first_half, 4),
                "distance_km": round((act.distance or 0) / 1000, 1),
                "workout_type": met.workout_type,
            })

    vo2max_trend = sorted([
        {"week_start": wk, "vdot": round(max(vals), 1)}
        for wk, vals in vo2max_by_week.items()
    ], key=lambda x: x["week_start"])

    decouple_trend = sorted([
        {"week_start": wk, "avg_decoupling": round(sum(vals) / len(vals), 2)}
        for wk, vals in decouple_by_week.items()
    ], key=lambda x: x["week_start"])

    pacing_trend = sorted([
        {"week_start": wk, "avg_pacing_cv": round(sum(vals) / len(vals), 2)}
        for wk, vals in pacing_by_week.items()
    ], key=lambda x: x["week_start"])

    # Garmin watch VO2max (weekly avg)
    garmin_vo2max = []
    garmin_result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start,
            GarminDailyHealth.vo2max_running.isnot(None),
        )
    )
    garmin_vo2_by_week = defaultdict(list)
    for g in garmin_result.scalars().all():
        garmin_vo2_by_week[_week_monday(g.date)].append(g.vo2max_running)
    garmin_vo2max = sorted([
        {"week_start": wk, "vo2max": round(sum(vals) / len(vals), 1)}
        for wk, vals in garmin_vo2_by_week.items()
    ], key=lambda x: x["week_start"])

    return {
        "vo2max": vo2max_trend,
        "garmin_vo2max": garmin_vo2max,
        "pace_decoupling": decouple_trend,
        "pacing_cv": pacing_trend,
        "ef_per_activity": ef_points,
    }


@router.get("/all_time_stats")
async def get_all_time_stats(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """All-time aggregate stats for the athlete."""
    athlete_id = _get_athlete_id(request)

    result = await db.execute(
        select(
            func.count(Activity.id).label("total_runs"),
            func.sum(Activity.distance).label("total_distance"),
            func.sum(Activity.moving_time).label("total_time"),
            func.sum(Activity.total_elevation_gain).label("total_elevation"),
            func.min(Activity.start_date).label("first_date"),
        ).where(Activity.athlete_id == athlete_id)
    )
    row = result.one()

    # Current streak: consecutive days with at least 1 activity
    streak_result = await db.execute(
        text("""
            SELECT DISTINCT substr(start_date, 1, 10) as run_date
            FROM activities
            WHERE athlete_id = :aid
            ORDER BY run_date DESC
        """),
        {"aid": athlete_id},
    )
    run_dates = [r[0] for r in streak_result.fetchall()]
    current_streak = 0
    if run_dates:
        from datetime import date as dt_date
        from datetime import timedelta
        check = dt_date.today()
        for d_str in run_dates:
            d = dt_date.fromisoformat(d_str)
            if d == check or d == check - timedelta(days=1):
                current_streak += 1
                check = d - timedelta(days=1)
            else:
                break

    return {
        "total_runs": row.total_runs or 0,
        "total_distance_km": round((row.total_distance or 0) / 1000, 1),
        "total_time_hr": round((row.total_time or 0) / 3600, 1),
        "total_elevation_m": round(row.total_elevation or 0, 0),
        "first_activity_date": (row.first_date or "")[:10],
        "current_streak_days": current_streak,
    }


@router.get("/pace_zones")
async def get_pace_zones(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Jack Daniels VDOT-based pace zones + HR zones.
    """
    import math

    from app.config import get_athlete_settings, get_settings
    from app.models.schema import Athlete

    athlete_id = _get_athlete_id(request)

    # Load athlete for settings
    ath_result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = ath_result.scalar_one_or_none()
    s = get_athlete_settings(athlete) if athlete else get_settings()

    # VO2max for pace zones
    vo2max = await compute_vo2max_estimate(db, athlete_id, max_hr=s.max_hr)

    # HR zones
    hrr = s.max_hr - s.resting_hr
    if s.hr_zone_method == "karvonen":
        boundaries = [round(s.resting_hr + p * hrr) for p in (0.60, 0.70, 0.80, 0.90)]
    else:
        boundaries = [round(s.max_hr * p) for p in (0.60, 0.70, 0.80, 0.90)]

    hr_zones = [
        {"zone": "Z1", "name": "Recovery", "min_hr": 0, "max_hr": boundaries[0] - 1},
        {"zone": "Z2", "name": "Aerobic", "min_hr": boundaries[0], "max_hr": boundaries[1] - 1},
        {"zone": "Z3", "name": "Tempo", "min_hr": boundaries[1], "max_hr": boundaries[2] - 1},
        {"zone": "Z4", "name": "Threshold", "min_hr": boundaries[2], "max_hr": boundaries[3] - 1},
        {"zone": "Z5", "name": "VO2max", "min_hr": boundaries[3], "max_hr": s.max_hr},
    ]

    # Pace zones from VDOT (Jack Daniels approximation)
    pace_zones = []
    if vo2max and vo2max > 20:
        # Velocity at different %VO2max intensities
        # Using Daniels' formula inverted: v = f(VDOT, %intensity)
        def _pace_at_pct(vdot, pct, duration_min=30):
            """Approximate pace (sec/km) at a given %VO2max."""
            target_vo2 = vdot * pct
            # Invert: VO2 = -4.6 + 0.182258*v + 0.000104*v^2
            # 0.000104*v^2 + 0.182258*v - (4.6 + target_vo2) = 0
            a = 0.000104
            b = 0.182258
            c = -(4.6 + target_vo2)
            disc = b**2 - 4*a*c
            if disc < 0:
                return None
            v = (-b + math.sqrt(disc)) / (2*a)  # m/min
            if v <= 0:
                return None
            return round(1000 / v * 60, 0)  # sec/km

        def _fmt_pace(s):
            if not s:
                return "–"
            m = int(s // 60)
            sec = int(s % 60)
            return f"{m}:{sec:02d}"

        zones_def = [
            ("Easy", 0.59, 0.74),
            ("Marathon", 0.75, 0.84),
            ("Threshold", 0.83, 0.88),
            ("Interval", 0.95, 1.00),
            ("Repetition", 1.05, 1.10),
        ]
        for name, lo_pct, hi_pct in zones_def:
            fast = _pace_at_pct(vo2max, hi_pct)
            slow = _pace_at_pct(vo2max, lo_pct)
            pace_zones.append({
                "name": name,
                "min_pace_sec": fast,  # fast = lower number
                "max_pace_sec": slow,  # slow = higher number
                "min_pace_str": _fmt_pace(fast),
                "max_pace_str": _fmt_pace(slow),
            })

    return {
        "hr_zones": hr_zones,
        "pace_zones": pace_zones,
        "vo2max": round(vo2max, 1) if vo2max else None,
        "max_hr": s.max_hr,
        "resting_hr": s.resting_hr,
        "hr_zone_method": s.hr_zone_method,
    }


@router.get("/recovery_history")
async def get_recovery_history(
    request: Request,
    days: int = 90,
    db: AsyncSession = Depends(get_db),
):
    """Daily readiness scores blending TSB + Garmin health data."""
    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()

    # Batch-fetch fitness + garmin data
    fit_result = await db.execute(
        select(DailyFitness).where(
            DailyFitness.athlete_id == athlete_id,
            DailyFitness.date >= start,
            DailyFitness.date <= end,
        ).order_by(DailyFitness.date)
    )
    fitness_rows = {r.date: r for r in fit_result.scalars().all()}

    garmin_result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start,
            GarminDailyHealth.date <= end,
        )
    )
    garmin_rows = {r.date: r for r in garmin_result.scalars().all()}

    out = []
    current = date.today() - timedelta(days=days)
    while current <= date.today():
        d_str = current.isoformat()
        f = fitness_rows.get(d_str)
        g = garmin_rows.get(d_str)

        tsb = f.tsb if f and f.tsb is not None else 0
        base_score = max(0, min(100, int(50 + tsb * 2)))
        acwr = (f.atl / f.ctl) if f and f.ctl and f.ctl > 0 else None
        if acwr is not None and acwr > 1.3:
            base_score = max(0, base_score - 15)

        sources = ["tsb"]
        garmin_factors = None
        score = base_score

        if g and g.training_readiness is not None:
            sleep_norm = 50.0
            if g.sleep_duration_sec:
                hours = g.sleep_duration_sec / 3600
                sleep_norm = max(0, min(100, (hours - 4) / 5 * 100))
            battery = g.body_battery_latest or 50
            score = max(0, min(100, int(
                0.4 * base_score + 0.3 * g.training_readiness + 0.15 * sleep_norm + 0.15 * battery
            )))
            sources = ["tsb", "garmin"]
            garmin_factors = {
                "training_readiness": g.training_readiness,
                "body_battery": g.body_battery_latest,
                "sleep_hours": round(g.sleep_duration_sec / 3600, 1) if g.sleep_duration_sec else None,
                "hrv_last_night": g.hrv_last_night,
                "resting_hr": g.resting_hr,
            }

        if score >= 80:
            label = "Fresh"
        elif score >= 60:
            label = "Ready"
        elif score >= 40:
            label = "Tired"
        else:
            label = "Fatigued"

        out.append({
            "date": d_str,
            "readiness_score": score,
            "label": label,
            "tsb": round(tsb, 1),
            "sources": sources,
            "garmin_factors": garmin_factors,
        })
        current += timedelta(days=1)

    return {"days": out}


@router.get("/health_correlations")
async def get_health_correlations(
    request: Request,
    weeks: int = 26,
    db: AsyncSession = Depends(get_db),
):
    """Correlation datasets: sleep vs EF, HRV vs VDOT, resting HR vs RSS."""
    from collections import defaultdict

    athlete_id = _get_athlete_id(request)
    start = (date.today() - timedelta(weeks=weeks)).isoformat()

    # Fetch activities with metrics
    act_result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(Activity.athlete_id == athlete_id, Activity.start_date >= start)
        .order_by(Activity.start_date)
    )
    activities = act_result.all()

    # Fetch Garmin health data
    garmin_result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start,
        )
    )
    garmin_by_date = {r.date: r for r in garmin_result.scalars().all()}

    if not garmin_by_date:
        return {"sleep_ef": [], "hrv_vdot": [], "rhr_rss": []}

    # 1. Sleep vs EF (per activity, easy/moderate/long_run only)
    sleep_ef = []
    for act, met in activities:
        if met.workout_type not in ("easy", "moderate", "long_run"):
            continue
        if not met.ef_first_half:
            continue
        act_date = (act.start_date_local or act.start_date or "")[:10]
        g = garmin_by_date.get(act_date)
        if g and g.sleep_duration_sec:
            sleep_ef.append({
                "sleep_hours": round(g.sleep_duration_sec / 3600, 1),
                "ef": round(met.ef_first_half, 4),
                "date": act_date,
                "workout_type": met.workout_type,
            })

    # 2. HRV vs VDOT (weekly)
    def _week_monday(d_str):
        d = date.fromisoformat(d_str[:10])
        return (d - timedelta(days=d.weekday())).isoformat()

    hrv_by_week = defaultdict(list)
    for d_str, g in garmin_by_date.items():
        if g.hrv_last_night is not None:
            hrv_by_week[_week_monday(d_str)].append(g.hrv_last_night)

    vdot_by_week = defaultdict(list)
    for act, met in activities:
        if met.estimated_vdot and met.estimated_vdot > 0:
            vdot_by_week[_week_monday(act.start_date)].append(met.estimated_vdot)

    hrv_vdot = sorted([
        {
            "week_start": wk,
            "avg_hrv": round(sum(vals) / len(vals), 1),
            "max_vdot": round(max(vdot_by_week[wk]), 1),
        }
        for wk, vals in hrv_by_week.items()
        if wk in vdot_by_week
    ], key=lambda x: x["week_start"])

    # 3. Resting HR vs RSS (weekly)
    rhr_by_week = defaultdict(list)
    for d_str, g in garmin_by_date.items():
        if g.resting_hr is not None:
            rhr_by_week[_week_monday(d_str)].append(g.resting_hr)

    rss_by_week = defaultdict(float)
    for act, met in activities:
        if met.rss:
            rss_by_week[_week_monday(act.start_date)] += met.rss

    rhr_rss = sorted([
        {
            "week_start": wk,
            "avg_resting_hr": round(sum(vals) / len(vals), 1),
            "total_rss": round(rss_by_week.get(wk, 0), 1),
        }
        for wk, vals in rhr_by_week.items()
        if wk in rss_by_week
    ], key=lambda x: x["week_start"])

    return {"sleep_ef": sleep_ef, "hrv_vdot": hrv_vdot, "rhr_rss": rhr_rss}

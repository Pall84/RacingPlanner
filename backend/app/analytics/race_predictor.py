"""
Race time prediction and race plan generation.

Three methods are combined:
  1. Riegel scaling from personal records
  2. Course simulation from recent training GAP pace
  3. VDOT-based estimate

A TSB form adjustment is applied to the weighted combination.
"""
import math
from datetime import date, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema import (
    Activity,
    ActivityMetrics,
    Athlete,
    DailyFitness,
    GarminDailyHealth,
    PersonalRecord,
)

# ── Minetti energy cost ───────────────────────────────────────────────────────

def _minetti(grade: float) -> float:
    """Energy cost of running at given grade (decimal, clamped ±0.45).
    Returns J/kg/m. Flat baseline = 3.6."""
    g = max(-0.45, min(0.45, grade))
    return 155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6


_C_FLAT = _minetti(0.0)   # ≈ 3.6


def grade_penalty(avg_grade_pct: float) -> float:
    """Multiply a flat GAP pace by this to get actual pace for given grade."""
    return _minetti(avg_grade_pct / 100.0) / _C_FLAT


# ── Riegel formula ────────────────────────────────────────────────────────────

def riegel(t1_sec: float, d1_m: float, d2_m: float) -> float:
    """Predict T2 given known T1 over D1, scaled to D2."""
    return t1_sec * (d2_m / d1_m) ** 1.06


# ── Distance key → (value_type, typical_distance_m) mapping ──────────────────

_PR_DISTANCES = {
    "best_5k":             ("time", 5000),
    "best_10k":            ("time", 10000),
    "best_half_marathon":  ("time", 21097),
    "best_marathon":       ("time", 42195),
}

# Pace (m/s) records — value = speed, need to convert to time
_PR_SPEED_RECORDS = {
    "fastest_pace": ("speed", None),
    "longest_run":  ("distance", None),
}


async def _riegel_from_prs(
    db: AsyncSession, athlete_id: int, target_dist_m: float
) -> dict | None:
    """
    Find the closest personal record and apply Riegel to predict target distance time.
    Returns {"time_sec": ..., "confidence": "high"|"medium"|"low", "source": ...} or None.
    """
    result = await db.execute(
        select(PersonalRecord).where(PersonalRecord.athlete_id == athlete_id)
    )
    prs = {r.record_type: r for r in result.scalars().all()}

    best_ratio = None
    best_pr = None
    best_pr_type = None

    for pr_type, (val_type, pr_dist) in _PR_DISTANCES.items():
        if pr_type not in prs:
            continue
        pr = prs[pr_type]
        ratio = abs(pr_dist - target_dist_m) / target_dist_m
        if best_ratio is None or ratio < best_ratio:
            best_ratio = ratio
            best_pr = pr
            best_pr_type = (pr_type, val_type, pr_dist)

    if best_pr is None or best_ratio is None or best_ratio > 0.6:
        # No usable PR
        return None

    _, val_type, pr_dist = best_pr_type
    t1 = best_pr.value  # stored as time_sec for distance PRs

    pred = riegel(t1, pr_dist, target_dist_m)
    confidence = "high" if best_ratio < 0.2 else "medium"

    return {
        "time_sec": pred,
        "confidence": confidence,
        "source": f"Riegel from {best_pr_type[0].replace('_', ' ')} ({_fmt_time(t1)})",
        "pr_dist_m": pr_dist,
        "pr_time_sec": t1,
    }


def _compute_gap_for_rows(
    rows: list,
    target_dist_m: float,
    course_km_splits: list,
) -> float | None:
    """
    Core course-simulation computation given pre-fetched (Activity, ActivityMetrics) rows.

    Identical math to _course_simulate after its DB fetch:
      weighted GAP pace → Riegel distance scaling → per-segment grade adjustment.

    Returns predicted_time_sec or None if fewer than 2 activities with valid data.
    Used by both _course_simulate (single point) and compute_prediction_history (bulk).
    """
    if not rows:
        return None

    gap_paces = []
    weights = []
    for i, (act, met) in enumerate(rows):
        # The caller filters rows where avg_gap_sec_per_km IS NOT NULL, but
        # being defensive here means a future filter change (or a stale row
        # slipping through) won't crash the multiply below with a None pace.
        if met.avg_gap_sec_per_km is None:
            continue
        z3 = met.z3_seconds or 0
        z4 = met.z4_seconds or 0
        z_total = (met.z1_seconds or 0) + (met.z2_seconds or 0) + z3 + z4 + (met.z5_seconds or 0)
        effort_weight = 1.0
        if z_total > 0:
            aerobic_fraction = (z3 + z4) / z_total
            effort_weight = 0.5 + aerobic_fraction

        recency_weight = math.exp(-0.1 * i)
        gap_paces.append(met.avg_gap_sec_per_km)
        weights.append(recency_weight * effort_weight)

    total_w = sum(weights)
    if total_w == 0 or len(rows) < 2:
        return None

    aerobic_gap = sum(p * w for p, w in zip(gap_paces, weights)) / total_w

    avg_train_dist = sum(act.distance or 0 for act, _ in rows) / len(rows)
    ref_dist = max(avg_train_dist, 5000)
    ref_time = aerobic_gap * (ref_dist / 1000)
    pred_time_flat = riegel(ref_time, ref_dist, target_dist_m)

    if course_km_splits:
        target_gap_pace = pred_time_flat / (target_dist_m / 1000)
        actual_time = 0.0
        for km in course_km_splits:
            km_dist = km.get("distance_m", 1000)
            grade = km.get("avg_grade_pct", 0.0)
            penalty = grade_penalty(grade)
            actual_time += target_gap_pace * penalty * (km_dist / 1000)
        return actual_time
    return pred_time_flat


async def _course_simulate(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
    course_km_splits: list[dict], settings,
) -> dict | None:
    """
    Predict using course simulation:
    1. Derive athlete's aerobic threshold GAP pace from recent Z3/Z4 training.
    2. Scale to race distance with Riegel distance fatigue.
    3. Apply per-km grade penalties from course GPX.
    """
    from datetime import timedelta
    cutoff_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")

    result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= cutoff_date,
            ActivityMetrics.avg_gap_sec_per_km.isnot(None),
            ActivityMetrics.avg_gap_sec_per_km > 0,
        )
        .order_by(desc(Activity.start_date))
        .limit(30)
    )
    rows = result.all()

    if not rows:
        # Fall back to all time
        result2 = await db.execute(
            select(Activity, ActivityMetrics)
            .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
            .where(
                Activity.athlete_id == athlete_id,
                ActivityMetrics.avg_gap_sec_per_km.isnot(None),
                ActivityMetrics.avg_gap_sec_per_km > 0,
            )
            .order_by(desc(Activity.start_date))
            .limit(20)
        )
        rows = result2.all()

    if not rows:
        return None

    pred_time = _compute_gap_for_rows(rows, target_dist_m, course_km_splits)
    if pred_time is None:
        return None

    # Reconstruct aerobic_gap for the source string
    gap_paces = [met.avg_gap_sec_per_km for _, met in rows]
    weights = [math.exp(-0.1 * i) for i in range(len(rows))]
    aerobic_gap = sum(p * w for p, w in zip(gap_paces, weights)) / sum(weights)

    confidence = "high" if len(rows) >= 5 else "medium"
    return {
        "time_sec": pred_time,
        "confidence": confidence,
        "source": f"Course simulation ({len(rows)} recent runs, aerobic GAP {_fmt_pace(aerobic_gap)})",
        "aerobic_gap_pace": aerobic_gap,
        "run_count": len(rows),
    }


async def _vdot_predict(
    db: AsyncSession, athlete_id: int, target_dist_m: float
) -> dict | None:
    """
    Use athlete's estimated VO2max (stored on Athlete row) to predict race time.
    Derives velocity at marathon effort and scales to target distance.
    """
    result = await db.execute(
        select(Athlete).where(Athlete.id == athlete_id)
    )
    athlete = result.scalar_one_or_none()
    if not athlete:
        return None

    vo2max = getattr(athlete, "estimated_vo2max", None)
    if not vo2max or vo2max <= 0:
        return None

    # Jack Daniels: at marathon effort ~80% VO2max
    # v_marathon ≈ (VO2max * 0.80 + 4.6) / 0.182258  ... simplified linear inversion
    # More accurate: solve numerically for the velocity that satisfies the VDOT formula
    # at ~240 min duration (marathon). We use the approximate formula:
    # VDOT = (-4.6 + 0.182258v + 0.000104v^2) / denominator
    # At marathon (t≈240min): denominator ≈ 0.8 + 0.1894 * e^(-0.012778*240) + 0.2990 * e^(-0.1933*240)
    #                                     ≈ 0.8 + ~0.00027 + ~0.0 ≈ 0.8
    # So VDOT ≈ (-4.6 + 0.182258v + 0.000104v^2) / 0.8
    # => 0.8 * VDOT = -4.6 + 0.182258v + 0.000104v^2
    # => 0.000104v^2 + 0.182258v - (4.6 + 0.8*VDOT) = 0

    a = 0.000104
    b = 0.182258
    c = -(4.6 + 0.8 * vo2max)
    discriminant = b**2 - 4 * a * c
    if discriminant < 0:
        return None

    v_marathon = (-b + math.sqrt(discriminant)) / (2 * a)  # m/min
    v_marathon_ms = v_marathon / 60.0

    # Marathon time at this pace
    marathon_dist = 42195.0
    marathon_time = marathon_dist / v_marathon_ms

    # Scale to target distance with Riegel
    pred_time = riegel(marathon_time, marathon_dist, target_dist_m)

    return {
        "time_sec": pred_time,
        "confidence": "medium",
        "source": f"VDOT estimate (VO2max ≈ {vo2max:.1f})",
        "vo2max": vo2max,
        "v_marathon_min_per_km": 1000 / v_marathon_ms / 60,
    }


async def _tsb_adjustment(
    db: AsyncSession, athlete_id: int, race_date_str: str
) -> tuple[float, float | None]:
    """
    Returns (adjustment_factor, tsb_value).
    adjustment_factor < 1 means faster (good form), > 1 means slower (fatigued).
    """
    result = await db.execute(
        select(DailyFitness)
        .where(
            DailyFitness.athlete_id == athlete_id,
            DailyFitness.date <= race_date_str,
        )
        .order_by(desc(DailyFitness.date))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if not row or row.tsb is None:
        return 1.0, None

    tsb = row.tsb
    # Linear scale: TSB=+15 → −2%, TSB=0 → 0%, TSB=−10 → +3%
    if tsb >= 0:
        factor = 1.0 - min(tsb / 15, 1.0) * 0.02
    else:
        factor = 1.0 + min(abs(tsb) / 10, 1.0) * 0.03

    return factor, tsb


# ── Garmin health adjustment ─────────────────────────────────────────────────

async def get_pre_race_health(
    db: AsyncSession, athlete_id: int, race_date: str, days: int = 7,
) -> dict | None:
    """Compute 7-day health averages leading into a race."""
    from datetime import timedelta
    race_d = date.fromisoformat(race_date[:10])
    start_d = (race_d - timedelta(days=days)).isoformat()
    end_d = (race_d - timedelta(days=1)).isoformat()

    result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start_d,
            GarminDailyHealth.date <= end_d,
        )
    )
    rows = result.scalars().all()
    if len(rows) < 3:
        return None

    def _avg(vals):
        filtered = [v for v in vals if v is not None]
        return sum(filtered) / len(filtered) if filtered else None

    from collections import Counter
    statuses = [r.hrv_status for r in rows if r.hrv_status]
    hrv_status_mode = Counter(statuses).most_common(1)[0][0] if statuses else None

    return {
        "avg_training_readiness": _avg([r.training_readiness for r in rows]),
        "avg_sleep_hours": _avg([r.sleep_duration_sec / 3600 if r.sleep_duration_sec else None for r in rows]),
        "avg_sleep_score": _avg([r.sleep_score for r in rows]),
        "avg_hrv": _avg([r.hrv_last_night for r in rows]),
        "hrv_status_mode": hrv_status_mode,
        "avg_body_battery": _avg([r.body_battery_latest for r in rows]),
        "avg_resting_hr": _avg([r.resting_hr for r in rows]),
        "data_days": len(rows),
    }


def _health_adjustment(health_data: dict | None) -> tuple[float, dict | None]:
    """
    Returns (adjustment_factor, health_factors_dict).
    Factor < 1 = faster (good health), > 1 = slower (poor health).
    Independent of TSB — uses only Garmin-specific signals.
    """
    if not health_data:
        return 1.0, None

    adj = 0.0

    # HRV status
    status = health_data.get("hrv_status_mode")
    if status == "LOW":
        adj += 0.01
    elif status and status not in ("BALANCED",):
        adj += 0.005

    # Sleep quality
    sleep_score = health_data.get("avg_sleep_score")
    if sleep_score is not None:
        if sleep_score > 80:
            adj -= 0.005
        elif sleep_score < 50:
            adj += 0.01

    # Body battery
    battery = health_data.get("avg_body_battery")
    if battery is not None:
        if battery >= 70:
            adj -= 0.005
        elif battery < 30:
            adj += 0.005

    # Training readiness
    readiness = health_data.get("avg_training_readiness")
    if readiness is not None:
        if readiness > 70:
            adj -= 0.005
        elif readiness < 40:
            adj += 0.005

    # Clamp total to [-2%, +3%]
    adj = max(-0.02, min(0.03, adj))

    return 1.0 + adj, {
        "hrv_status": status,
        "avg_sleep_score": round(sleep_score, 1) if sleep_score else None,
        "avg_body_battery": round(battery, 0) if battery else None,
        "avg_training_readiness": round(readiness, 0) if readiness else None,
        "adjustment_pct": round(adj * 100, 1),
    }


# ── Recent race ensemble method (user-marked) ────────────────────────────────

async def _recent_race_predict(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
) -> dict | None:
    """Predict from the athlete's most recent user-marked race.

    Strong signal: a race is the athlete's ground-truth performance at that
    distance under real race-day conditions (pacing, nutrition, competitive
    effort). Unlike Strava PRs — which can be fast training runs labeled
    "5k" — an `is_race` flag is explicit intent.

    Selection:
      - Last 12 months only (older races reflect prior fitness)
      - Prefer races within ±20% of target distance (the Riegel exponent
        stays reasonable close to the source distance)
      - Among eligible, pick the most recent (fitness > old-race distance match)

    Confidence is "high" when the distance match is close (< 20% deviation),
    otherwise "medium". This can out-weight the Riegel-from-PRs method when
    the PR is stale — which is usually correct.
    """
    # Div-by-zero guard: _distance_score divides by target_dist_m. A zero
    # target (upstream parsing error) would crash on the first candidate.
    if not target_dist_m or target_dist_m <= 0:
        return None

    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")

    result = await db.execute(
        select(Activity)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.is_race == 1,
            Activity.start_date >= cutoff,
            Activity.distance > 0,
            Activity.moving_time > 0,
        )
        .order_by(desc(Activity.start_date))
        .limit(10)
    )
    races = result.scalars().all()
    if not races:
        return None

    # Score each candidate: smaller distance deviation is better; recency
    # breaks ties. Riegel gets unreliable past ~60% distance deviation, so
    # exclude anything further out than that — better to fall back to PRs.
    def _distance_score(r: Activity) -> float:
        return abs(r.distance - target_dist_m) / target_dist_m

    viable = [r for r in races if _distance_score(r) < 0.6]
    if not viable:
        return None

    # Prefer close distance match unless it's >30 days stale AND a closer
    # race is available. Beyond that, trust recency.
    viable.sort(key=lambda r: (_distance_score(r), -_iso_timestamp(r.start_date)))
    best = viable[0]

    dev = _distance_score(best)
    confidence = "high" if dev < 0.2 else "medium"

    pred_time = riegel(float(best.moving_time), float(best.distance), target_dist_m)

    race_date_short = (best.start_date or "")[:10]
    race_dist_km = best.distance / 1000
    race_name = (best.name or "race").strip() or "race"
    # Truncate for the source string
    if len(race_name) > 40:
        race_name = race_name[:37] + "…"

    return {
        "time_sec": pred_time,
        "confidence": confidence,
        "source": f"Marked race: {race_name} — {race_dist_km:.1f}km in {_fmt_time(best.moving_time)} ({race_date_short})",
        "race_activity_id": best.id,
        "race_distance_m": best.distance,
        "race_time_sec": best.moving_time,
        "race_date": race_date_short,
        "distance_deviation_pct": round(dev * 100, 1),
    }


def _iso_timestamp(iso_str: str) -> float:
    """Convert a Strava ISO timestamp to a sortable float. Returns 0 on parse fail."""
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


# ── Lactate threshold pace ensemble method ────────────────────────────────────

async def _lt_pace_predict(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
) -> dict | None:
    """Predict race time from Garmin's lactate threshold pace.

    LT pace is the pace an athlete can sustain at their lactate threshold —
    empirically ~45–60 min of maximal effort for well-trained runners. This
    is a physiologically-grounded time-trial reference point that's often
    more current than stale race PRs.

    We treat LT pace as a synthetic "50-min race" and scale via Riegel:
        T₁ = 3000 s  (50 min)
        D₁ = T₁ × LT_speed_ms

    Then standard Riegel to the target distance. This is "high" confidence
    because Garmin calibrates LT from actual runs with HR zone patterns,
    not extrapolated from VO2max estimates.

    Staleness check: LT pace older than 30 days isn't used — the athlete
    has likely progressed or regressed since then.
    """
    result = await db.execute(
        select(GarminDailyHealth)
        .where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.lactate_threshold_speed_ms.isnot(None),
        )
        .order_by(desc(GarminDailyHealth.date))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None or not row.lactate_threshold_speed_ms:
        return None

    from datetime import date as _date
    try:
        data_date = _date.fromisoformat(row.date)
        days_old = (_date.today() - data_date).days
    except Exception:
        days_old = 999
    if days_old > 30:
        return None

    # Garmin returns LT "speed" as seconds per meter (a pace-like quantity,
    # matching how Garmin Connect's UI displays LT pace). Despite the API
    # field name and our column name ending in "_ms", it is NOT meters per
    # second. A value of 0.35 = 0.35 sec/m = 5:50/km; a value of 5.0 would
    # be nonsense (0.2 m/s = walking). The column name is kept for backward
    # compat with already-synced rows; it's reinterpreted here.
    lt_sec_per_m = float(row.lactate_threshold_speed_ms)

    # Sanity range: 0.12 s/m ≈ 2:00/km (faster than world-record 5K pace)
    #               0.60 s/m ≈ 10:00/km (slow jog — below meaningful LT)
    if lt_sec_per_m < 0.12 or lt_sec_per_m > 0.60:
        return None

    lt_pace_sec_per_km = lt_sec_per_m * 1000.0

    # Synthetic 50-min max LT effort: T₁ = 3000s, D₁ = T₁ / pace
    t1_sec = 50 * 60
    d1_m = t1_sec / lt_sec_per_m

    pred_time = riegel(t1_sec, d1_m, target_dist_m)

    return {
        "time_sec": pred_time,
        "confidence": "high",
        "source": f"LT pace ({_fmt_pace(lt_pace_sec_per_km)}, {days_old}d old)",
        "lt_sec_per_m": lt_sec_per_m,
        "lt_pace_sec_per_km": lt_pace_sec_per_km,
        "data_age_days": days_old,
    }


# ── Watch VO2max ensemble method ──────────────────────────────────────────────

async def _watch_vo2max_predict(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
) -> dict | None:
    """Predict from Garmin's watch-computed VO2max.

    Garmin calibrates VO2max per-athlete using HR + pace + grade + body metrics
    from real runs — it's usually closer to the truth than Daniels VDOT
    recomputed from arbitrary training runs. Used as a 4th ensemble method
    (not a modifier), so it *replaces variance* rather than stacking a
    correction on top of existing predictions.

    We reuse `_vdot_predict`'s algebra: solve the Daniels formula at t≈240 min
    for marathon velocity, then Riegel-scale to the target distance.
    """
    # Most recent Garmin VO2max within 30 days
    result = await db.execute(
        select(GarminDailyHealth)
        .where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.vo2max_running.isnot(None),
        )
        .order_by(desc(GarminDailyHealth.date))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None or not row.vo2max_running:
        return None

    # Staleness check — VO2max from a watch fit 6 months ago is not a good
    # predictor of current fitness.
    from datetime import date as _date
    try:
        data_date = _date.fromisoformat(row.date)
        days_old = (_date.today() - data_date).days
    except Exception:
        days_old = 999
    if days_old > 30:
        return None

    vo2max = float(row.vo2max_running)

    # Same marathon-velocity quadratic as _vdot_predict (see its comments).
    a = 0.000104
    b = 0.182258
    c = -(4.6 + 0.8 * vo2max)
    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return None

    v_marathon_min = (-b + math.sqrt(discriminant)) / (2 * a)  # m/min
    v_marathon_ms = v_marathon_min / 60.0
    if v_marathon_ms <= 0:
        return None

    marathon_dist = 42195.0
    marathon_time = marathon_dist / v_marathon_ms
    pred_time = riegel(marathon_time, marathon_dist, target_dist_m)

    return {
        "time_sec": pred_time,
        "confidence": "medium",
        "source": f"Garmin VO2max ({vo2max:.1f})",
        "vo2max": vo2max,
        "data_age_days": days_old,
    }


# ── Long-run decoupling (marathon-specific modifier) ──────────────────────────

async def _decoupling_adjustment(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
) -> tuple[float, dict | None]:
    """Slow down marathon+ predictions when recent long runs show heavy decoupling.

    Decoupling = pace-to-HR efficiency drop from first to second half. A
    well-trained aerobic system holds pace at stable HR; a poorly-trained one
    loses ~5-10% efficiency in the back half. This predicts late-race blowup
    better than any other single metric for marathon/ultra.

    Gated to target_dist >= 21 km — short races don't have a "back half that
    blows up" problem, and tempo/threshold runs are *supposed* to decouple.

    Looks at the 5 most recent runs that are both:
      - long (moving_time >= 75 min, i.e. substantial aerobic stress)
      - workout_type in (long_run, easy, moderate) — not tempo/threshold/VO2
    """
    if target_dist_m < 21000:
        return 1.0, None

    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")

    result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= cutoff,
            Activity.moving_time >= 75 * 60,
            ActivityMetrics.pace_decoupling_pct.isnot(None),
            ActivityMetrics.workout_type.in_(("long_run", "easy", "moderate")),
        )
        .order_by(desc(Activity.start_date))
        .limit(5)
    )
    rows = result.all()
    if len(rows) < 2:
        return 1.0, None

    decouple_values = [m.pace_decoupling_pct for _, m in rows if m.pace_decoupling_pct is not None]
    if len(decouple_values) < 2:
        return 1.0, None

    avg_decouple = sum(decouple_values) / len(decouple_values)

    # Translate to pace factor. Thresholds follow training literature:
    #   < 5%  : good aerobic durability, no penalty
    #   5-8%  : marginal, +1%
    #   8-12% : poor, +2%
    #   > 12% : severe blow-up risk, +3%
    # Capped at +3% so this modifier alone can't dominate.
    if avg_decouple < 5:
        adj = 0.0
    elif avg_decouple < 8:
        adj = 0.01
    elif avg_decouple < 12:
        adj = 0.02
    else:
        adj = 0.03

    factor = 1.0 + adj
    return factor, {
        "avg_decoupling_pct": round(avg_decouple, 1),
        "long_run_count": len(decouple_values),
        "adjustment_pct": round(adj * 100, 1),
    }


# ── Longest-run confidence gate ───────────────────────────────────────────────

async def _longest_run_confidence_widen(
    db: AsyncSession, athlete_id: int, target_dist_m: float,
) -> tuple[float, dict | None]:
    """Widen the confidence range when the longest recent run is short vs target.

    Returns a range-widening multiplier (>= 1.0), not a time modifier.
    Physiologically honest: the model extrapolates less reliably the further
    the race distance is beyond what the athlete has actually trained in.

    Checks longest moving_time-weighted run in last 8 weeks:
      - ratio >= 0.75 of target   → no widening
      - ratio 0.5 - 0.75          → widen ±3%
      - ratio < 0.5               → widen ±6%
    """
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=56)).strftime("%Y-%m-%dT00:00:00Z")

    result = await db.execute(
        select(Activity.distance)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= cutoff,
            Activity.distance > 0,
        )
        .order_by(desc(Activity.distance))
        .limit(1)
    )
    row = result.one_or_none()
    if row is None or not row[0]:
        return 1.0, None

    longest = float(row[0])
    ratio = longest / target_dist_m

    if ratio >= 0.75:
        widen = 1.0
    elif ratio >= 0.5:
        widen = 1.03
    else:
        widen = 1.06

    return widen, {
        "longest_run_m": round(longest, 0),
        "ratio": round(ratio, 2),
        "widen_pct": round((widen - 1.0) * 100, 1),
    }


# ── Weather adjustment (course conditions, not athlete state) ────────────────

async def _apply_weather_penalty(
    race_latlng: list, race_date: str,
) -> tuple[float, dict | None]:
    """Pull the race-day forecast and translate to a pace penalty.

    Only fetched for races within 14 days (forecast horizon). Beyond that,
    returns (1.0, None) — using climate averages for a specific race day is
    less reliable than ignoring weather.
    """
    from datetime import date as _date
    try:
        rd = _date.fromisoformat(race_date[:10])
        days_out = (rd - _date.today()).days
    except Exception:
        return 1.0, None

    if days_out < -1 or days_out > 14:
        # Too far out or already past → no forecast. (-1 covers same-day edge cases.)
        return 1.0, None

    from app.analytics.weather import (
        fetch_race_weather,
        midpoint_of_latlng,
        weather_pace_penalty,
    )
    mid = midpoint_of_latlng(race_latlng)
    if mid is None:
        return 1.0, None

    weather = await fetch_race_weather(mid[0], mid[1], race_date[:10])
    return weather_pace_penalty(weather)


# ── Confidence → numeric weight ───────────────────────────────────────────────

_CONF_WEIGHT = {"high": 3, "medium": 2, "low": 1}

# Max combined swing from all multiplicative modifiers stacked together.
# Prevents the "perfectly tapered athlete with great sleep and low
# decoupling" compounding case from predicting 15% faster than the
# ensemble's raw output. ±7% is the upper end of the literature for
# stacked form/health effects.
_COMBINED_MODIFIER_CAP = (0.93, 1.07)


async def predict_race_time(
    db: AsyncSession,
    athlete_id: int,
    distance_m: float,
    course_km_splits: list[dict],
    race_date: str,
    settings,
    race_latlng: list | None = None,
) -> dict:
    """
    Combine three prediction methods and apply form adjustment.

    Returns dict with keys:
      predicted_time_sec, predicted_pace_sec_per_km,
      breakdown: [{method, time_sec, confidence, source}, ...]
      tsb, tsb_adjustment_factor
    """
    methods = []

    # User-marked recent race comes first — if present at similar distance
    # it's usually the single best predictor (ground-truth race-day effort
    # from actual current fitness, not a stale PR or a fast training run).
    recent_race_result = await _recent_race_predict(db, athlete_id, distance_m)
    if recent_race_result:
        methods.append({"method": "recent_race", **recent_race_result})

    riegel_result = await _riegel_from_prs(db, athlete_id, distance_m)
    if riegel_result:
        methods.append({"method": "riegel", **riegel_result})

    sim_result = await _course_simulate(db, athlete_id, distance_m, course_km_splits, settings)
    if sim_result:
        methods.append({"method": "simulation", **sim_result})

    vdot_result = await _vdot_predict(db, athlete_id, distance_m)
    if vdot_result:
        methods.append({"method": "vdot", **vdot_result})

    # Watch VO2max is a 4th ensemble member, not a modifier — this avoids
    # the double-counting trap of stacking yet another multiplier on top of
    # the existing prediction. Ensemble weight is "medium" (same as VDOT).
    watch_result = await _watch_vo2max_predict(db, athlete_id, distance_m)
    if watch_result:
        methods.append({"method": "watch_vo2max", **watch_result})

    # Lactate threshold pace — 5th ensemble member, "high" confidence since
    # Garmin calibrates LT from actual HR-paced runs rather than extrapolating
    # from an estimated VO2max. When present this tends to dominate the
    # ensemble weighting, which is usually the right behavior for 10K+ races.
    lt_result = await _lt_pace_predict(db, athlete_id, distance_m)
    if lt_result:
        methods.append({"method": "lt_pace", **lt_result})

    if not methods:
        return {
            "predicted_time_sec": None,
            "predicted_pace_sec_per_km": None,
            "breakdown": [],
            "tsb": None,
            "tsb_adjustment_factor": 1.0,
            "error": "Insufficient training data for prediction",
        }

    # Weighted average
    total_w = sum(_CONF_WEIGHT[m["confidence"]] for m in methods)
    raw_time = sum(m["time_sec"] * _CONF_WEIGHT[m["confidence"]] for m in methods) / total_w

    tsb_factor, tsb = await _tsb_adjustment(db, athlete_id, race_date)
    health_data = await get_pre_race_health(db, athlete_id, race_date)
    health_factor, health_factors = _health_adjustment(health_data)
    decouple_factor, decouple_factors = await _decoupling_adjustment(
        db, athlete_id, distance_m,
    )

    # Weather: fetch forecast if race is within horizon AND we have course GPS.
    # This is a *course-conditions* factor, not an athlete-state factor, so we
    # apply it outside the combined-modifier cap (heat effects are additive to
    # form/health, not correlated). Still clamped to +5% internally.
    weather_factor, weather_info = 1.0, None
    if race_latlng:
        weather_factor, weather_info = await _apply_weather_penalty(race_latlng, race_date)

    # Combine athlete-state factors, then clamp cumulative swing.
    # TSB + health + decoupling can compound to ±10% if uncapped; physiological
    # literature tops out around ±7% for form/health stacked, so we clamp there.
    # Weather is applied separately since it's a course condition, not athlete state.
    combined = tsb_factor * health_factor * decouple_factor
    combined = max(_COMBINED_MODIFIER_CAP[0], min(_COMBINED_MODIFIER_CAP[1], combined))

    final_time = raw_time * combined * weather_factor
    final_pace = final_time / (distance_m / 1000)

    # Add formatted strings to breakdown for display
    for m in methods:
        m["time_str"] = _fmt_time(m["time_sec"])
        m["pace_str"] = _fmt_pace(m["time_sec"] / (distance_m / 1000))

    # Confidence range — derived from spread of method predictions
    if len(methods) >= 2:
        times = [m["time_sec"] for m in methods]
        min_time = min(times)
        max_time = max(times)
        avg_conf = sum(_CONF_WEIGHT[m["confidence"]] for m in methods) / len(methods)
        spread_factor = 1.0 + (3 - avg_conf) * 0.03
        range_low = min_time / spread_factor
        range_high = max_time * spread_factor
    else:
        range_low = final_time * 0.95
        range_high = final_time * 1.05

    # Narrow confidence range if good Garmin health data available
    if health_data and health_data.get("data_days", 0) >= 5:
        shrink = 0.98
        mid = (range_low + range_high) / 2
        range_low = mid + (range_low - mid) * shrink
        range_high = mid + (range_high - mid) * shrink

    # Widen if the athlete hasn't trained near the target distance. This is a
    # *range* change, not a time change — the ensemble's point estimate may
    # still be correct, we're just less certain.
    widen_factor, widen_info = await _longest_run_confidence_widen(
        db, athlete_id, distance_m,
    )
    if widen_factor > 1.0:
        mid = (range_low + range_high) / 2
        range_low = mid - (mid - range_low) * widen_factor
        range_high = mid + (range_high - mid) * widen_factor

    # Data quality assessment
    sim_run_count = 0
    if sim_result:
        sim_run_count = sim_result.get("run_count", 0)

    missing = []
    if not riegel_result:
        missing.append("No race PRs found — run a timed 5k/10k/half to improve predictions")
    if not sim_result:
        missing.append("No recent training data — sync recent runs from Strava")
    if not vdot_result:
        missing.append("No VO2max estimate — run more aerobic threshold efforts")
    if sim_result and sim_run_count < 5:
        missing.append(f"Only {sim_run_count} recent runs — more training data improves accuracy")

    # Sensitivity analysis
    sensitivity = {
        "effort_minus_10": round(final_time * 0.90),
        "effort_minus_10_str": _fmt_time(final_time * 0.90),
        "effort_minus_5": round(final_time * 0.95),
        "effort_minus_5_str": _fmt_time(final_time * 0.95),
        "effort_plus_5": round(final_time * 1.05),
        "effort_plus_5_str": _fmt_time(final_time * 1.05),
        "effort_plus_10": round(final_time * 1.10),
        "effort_plus_10_str": _fmt_time(final_time * 1.10),
    }

    return {
        "predicted_time_sec": final_time,
        "predicted_pace_sec_per_km": final_pace,
        "raw_time_sec": raw_time,
        "breakdown": methods,
        "tsb": tsb,
        "tsb_adjustment_factor": tsb_factor,
        "tsb_adjustment_pct": round((tsb_factor - 1.0) * 100, 1),
        "health_adjustment_factor": health_factor,
        "health_adjustment_pct": round((health_factor - 1.0) * 100, 1),
        "health_factors": health_factors,
        "health_data_available": health_data is not None,
        "decoupling_adjustment_factor": decouple_factor,
        "decoupling_adjustment_pct": round((decouple_factor - 1.0) * 100, 1),
        "decoupling_factors": decouple_factors,
        "weather_adjustment_factor": weather_factor,
        "weather_adjustment_pct": round((weather_factor - 1.0) * 100, 1),
        "weather_info": weather_info,
        "combined_modifier_factor": combined,
        "combined_modifier_pct": round((combined - 1.0) * 100, 1),
        "combined_modifier_capped": not (
            _COMBINED_MODIFIER_CAP[0] < tsb_factor * health_factor * decouple_factor < _COMBINED_MODIFIER_CAP[1]
        ),
        "confidence_widen_factor": widen_factor,
        "confidence_widen_info": widen_info,
        "range_low_sec": range_low,
        "range_high_sec": range_high,
        "range_low_str": _fmt_time(range_low),
        "range_high_str": _fmt_time(range_high),
        "data_quality": {
            "method_count": len(methods),
            "has_pr": bool(riegel_result),
            "has_simulation": bool(sim_result),
            "has_vdot": bool(vdot_result),
            "training_runs": sim_run_count,
            "missing": missing,
        },
        "sensitivity": sensitivity,
    }


# ── Race plan generation ──────────────────────────────────────────────────────

def generate_race_plan(
    predicted_time_sec: float,
    course_km_splits: list[dict],
    strategy: str = "even",
) -> list[dict]:
    """
    Generate a km-by-km race plan.

    strategy:
      "even"         — constant GAP effort throughout
      "negative"     — first half 4% slower GAP, second half 4% faster
      "conservative" — first 30% at +8%, middle 40% baseline, final 30% at −4%

    Returns list of dicts per km.
    """
    if not course_km_splits or not predicted_time_sec:
        return []

    total_dist = sum(km.get("distance_m", 1000) for km in course_km_splits)
    if total_dist <= 0:
        return []

    # Baseline target GAP pace (flat equivalent pace)
    baseline_gap = predicted_time_sec / (total_dist / 1000)

    # Build effort multipliers per km based on strategy
    cum = 0.0
    plan = []

    for i, km in enumerate(course_km_splits):
        km_dist = km.get("distance_m", 1000)
        grade = km.get("avg_grade_pct", 0.0)
        ele_gain = km.get("elevation_gain", 0.0)
        ele_loss = km.get("elevation_loss", 0.0)
        ele_net = km.get("elevation_net", round(ele_gain - ele_loss, 1))

        # Effort multiplier based on strategy and position in race
        cum_after = cum + km_dist
        race_pct = (cum + km_dist / 2) / total_dist  # midpoint of this km

        if strategy == "negative":
            if race_pct < 0.5:
                effort_mult = 1.04
            else:
                effort_mult = 0.96
        elif strategy == "conservative":
            if race_pct < 0.30:
                effort_mult = 1.08
            elif race_pct < 0.70:
                effort_mult = 1.00
            else:
                effort_mult = 0.96
        else:  # "even"
            effort_mult = 1.00

        target_gap = baseline_gap * effort_mult
        penalty = grade_penalty(grade)
        target_actual_pace = target_gap * penalty
        effort_pct = round((1.0 / effort_mult) * 100, 1)  # higher = harder effort

        plan.append({
            "km_index": km.get("km_index", i + 1),
            "distance_m": round(km_dist, 0),
            "elevation_gain": round(ele_gain, 1),
            "elevation_loss": round(ele_loss, 1),
            "elevation_net": round(ele_net, 1),
            "avg_grade_pct": round(grade, 2),
            "target_gap_pace": round(target_gap, 1),
            "target_gap_pace_str": _fmt_pace(target_gap),
            "target_actual_pace": round(target_actual_pace, 1),
            "target_actual_pace_str": _fmt_pace(target_actual_pace),
            "effort_pct": effort_pct,
            "cum_distance_m": round(cum_after, 0),
        })
        cum = cum_after

    return plan


def generate_all_strategies(
    predicted_time_sec: float,
    course_km_splits: list[dict],
    distance_m: float,
) -> dict:
    """
    Generate race plans for all three strategies and return a comparison summary.
    """
    if not course_km_splits or not predicted_time_sec:
        return {"strategies": []}

    total_dist = sum(km.get("distance_m", 1000) for km in course_km_splits)
    strategies = []

    for name, label in [("even", "Even Effort"), ("negative", "Negative Split"), ("conservative", "Conservative")]:
        plan = generate_race_plan(predicted_time_sec, course_km_splits, name)
        if not plan:
            continue

        # Compute total actual time from the plan
        total_time = 0.0
        for seg in plan:
            seg_dist_km = seg["distance_m"] / 1000
            total_time += seg["target_actual_pace"] * seg_dist_km

        # Half split times
        half_dist = total_dist / 2
        first_half_sec = 0.0
        cum = 0.0
        for seg in plan:
            seg_dist = seg["distance_m"]
            if cum + seg_dist <= half_dist:
                first_half_sec += seg["target_actual_pace"] * (seg_dist / 1000)
                cum += seg_dist
            else:
                remaining = half_dist - cum
                first_half_sec += seg["target_actual_pace"] * (remaining / 1000)
                break
        second_half_sec = total_time - first_half_sec

        # Fastest and slowest segments
        fastest = min(plan, key=lambda s: s["target_actual_pace"])
        slowest = max(plan, key=lambda s: s["target_actual_pace"])

        strategies.append({
            "name": name,
            "label": label,
            "predicted_time_sec": round(total_time, 1),
            "predicted_time_str": _fmt_time(total_time),
            "predicted_pace_str": _fmt_pace(total_time / (distance_m / 1000)),
            "first_half_sec": round(first_half_sec, 1),
            "first_half_str": _fmt_time(first_half_sec),
            "second_half_sec": round(second_half_sec, 1),
            "second_half_str": _fmt_time(second_half_sec),
            "fastest_km": {
                "km_index": fastest["km_index"],
                "pace_str": fastest["target_actual_pace_str"],
                "grade": fastest["avg_grade_pct"],
            },
            "slowest_km": {
                "km_index": slowest["km_index"],
                "pace_str": slowest["target_actual_pace_str"],
                "grade": slowest["avg_grade_pct"],
            },
            "plan": plan,
        })

    return {"strategies": strategies}


# ── Prediction history (weekly trend toward race day) ─────────────────────────

async def compute_prediction_history(
    db: AsyncSession,
    athlete_id: int,
    distance_m: float,
    course_km_splits: list,
    race_date: str,
    settings,
    lookback_weeks: int = 20,
) -> dict:
    """
    Build a weekly time-series of predicted finish times leading up to race day.

    For each week from (today − lookback_weeks) to today, the prediction is
    reconstructed using only training data that existed at that point in time
    (same 90-day window as the live prediction).  Two bulk DB queries cover the
    entire lookback period; slicing and computation happen in Python.

    Returns:
        {
          "snapshots": [
              {"date", "predicted_time_sec", "predicted_time_str", "ctl", "tsb", "run_count"},
              ...
          ],
          "race_date": str,
          "current_predicted_time_sec": float | None,
          "current_predicted_time_str": str,
        }
    """
    from datetime import date as date_type
    from datetime import timedelta

    today = date_type.today()
    bulk_start = today - timedelta(weeks=lookback_weeks + 13)  # +13 ≈ 90 days extra
    bulk_start_str = bulk_start.strftime("%Y-%m-%dT00:00:00Z")

    # ── Bulk fetch 1: all activities with metrics in the lookback window ────────
    result = await db.execute(
        select(Activity, ActivityMetrics)
        .join(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.athlete_id == athlete_id,
            Activity.start_date >= bulk_start_str,
            ActivityMetrics.avg_gap_sec_per_km.isnot(None),
            ActivityMetrics.avg_gap_sec_per_km > 0,
        )
        .order_by(desc(Activity.start_date))
    )
    all_rows = result.all()

    # Index by start_date string for fast filtering
    # Each row: (Activity, ActivityMetrics); act.start_date is ISO string
    def _act_date(row):
        return row[0].start_date[:10]  # YYYY-MM-DD

    # ── Bulk fetch 2: daily_fitness rows for the lookback window ───────────────
    fit_result = await db.execute(
        select(DailyFitness)
        .where(
            DailyFitness.athlete_id == athlete_id,
            DailyFitness.date >= bulk_start.isoformat(),
        )
        .order_by(DailyFitness.date)
    )
    fitness_rows = fit_result.scalars().all()
    # Dict keyed by date string for O(1) lookup
    fitness_by_date = {r.date: r for r in fitness_rows}
    fitness_dates_sorted = sorted(fitness_by_date.keys())

    def _nearest_fitness(snapshot_date_str: str):
        """Return the DailyFitness row on or before snapshot_date."""
        import bisect
        idx = bisect.bisect_right(fitness_dates_sorted, snapshot_date_str) - 1
        if idx < 0:
            return None
        return fitness_by_date[fitness_dates_sorted[idx]]

    # ── Weekly loop ────────────────────────────────────────────────────────────
    snapshots = []
    window_days = 90

    snapshot_date = today - timedelta(weeks=lookback_weeks)
    while snapshot_date <= today:
        snap_str = snapshot_date.isoformat()
        cutoff_str = (snapshot_date - timedelta(days=window_days)).isoformat()

        # Filter to activities within the 90-day window ending at snapshot_date
        window_rows = [
            row for row in all_rows
            if cutoff_str <= _act_date(row) <= snap_str
        ]
        # Sort by date desc (most recent first) and cap at 30 like live prediction
        window_rows = sorted(window_rows, key=_act_date, reverse=True)[:30]

        pred_time = _compute_gap_for_rows(window_rows, distance_m, course_km_splits)

        if pred_time is not None:
            fit = _nearest_fitness(snap_str)
            snapshots.append({
                "date": snap_str,
                "predicted_time_sec": round(pred_time, 1),
                "predicted_time_str": _fmt_time(pred_time),
                "ctl": round(fit.ctl, 1) if fit and fit.ctl is not None else None,
                "tsb": round(fit.tsb, 1) if fit and fit.tsb is not None else None,
                "run_count": len(window_rows),
            })

        snapshot_date += timedelta(weeks=1)

    current = snapshots[-1] if snapshots else {}
    return {
        "snapshots": snapshots,
        "race_date": race_date,
        "current_predicted_time_sec": current.get("predicted_time_sec"),
        "current_predicted_time_str": current.get("predicted_time_str", "–"),
    }


# ── Formatting helpers ────────────────────────────────────────────────────────
# Module-internal aliases — predictor code uses _fmt_* throughout; keep the
# names stable and delegate to the canonical implementations in
# analytics/formatters.py so there's one source of truth.
from app.analytics.formatters import fmt_pace as _fmt_pace
from app.analytics.formatters import fmt_time as _fmt_time

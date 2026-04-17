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


# ── Confidence → numeric weight ───────────────────────────────────────────────

_CONF_WEIGHT = {"high": 3, "medium": 2, "low": 1}


async def predict_race_time(
    db: AsyncSession,
    athlete_id: int,
    distance_m: float,
    course_km_splits: list[dict],
    race_date: str,
    settings,
) -> dict:
    """
    Combine three prediction methods and apply form adjustment.

    Returns dict with keys:
      predicted_time_sec, predicted_pace_sec_per_km,
      breakdown: [{method, time_sec, confidence, source}, ...]
      tsb, tsb_adjustment_factor
    """
    methods = []

    riegel_result = await _riegel_from_prs(db, athlete_id, distance_m)
    if riegel_result:
        methods.append({"method": "riegel", **riegel_result})

    sim_result = await _course_simulate(db, athlete_id, distance_m, course_km_splits, settings)
    if sim_result:
        methods.append({"method": "simulation", **sim_result})

    vdot_result = await _vdot_predict(db, athlete_id, distance_m)
    if vdot_result:
        methods.append({"method": "vdot", **vdot_result})

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
    final_time = raw_time * tsb_factor * health_factor
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

def _fmt_time(sec: float) -> str:
    if not sec or sec <= 0:
        return "–"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _fmt_pace(sec_per_km: float) -> str:
    if not sec_per_km or sec_per_km <= 0:
        return "–"
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    return f"{m}:{s:02d} /km"

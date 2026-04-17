"""
Workout classification engine.
Auto-classifies each activity into a workout type based on HR zone distribution,
pacing variability, and duration/distance relative to recent averages.

Also provides per-activity VDOT estimation using the Jack Daniels formula.
"""
import math
import re

# ── Workout classification ───────────────────────────────────────────────────

_RACE_KEYWORDS = re.compile(
    r"\b(race|marathon|parkrun|park run|5k|10k|half[-\s]?marathon|ultra|relay|triathlon)\b",
    re.IGNORECASE,
)


def classify_workout(
    metrics: dict,
    activity: dict,
    recent_avg_duration: float,
    recent_avg_distance: float,
) -> str:
    """
    Classify an activity into a workout type.

    Parameters:
        metrics: dict with keys z1_seconds..z5_seconds, pacing_cv_pct, etc.
                 (from ActivityMetrics row as dict)
        activity: dict with keys name, moving_time, distance
                  (from Activity row as dict)
        recent_avg_duration: average moving_time of the athlete's last 30 activities
        recent_avg_distance: average distance of the athlete's last 30 activities

    Returns one of:
        race, vo2max_intervals, threshold, tempo, long_run, moderate, recovery, easy
    """
    z1 = metrics.get("z1_seconds") or 0
    z2 = metrics.get("z2_seconds") or 0
    z3 = metrics.get("z3_seconds") or 0
    z4 = metrics.get("z4_seconds") or 0
    z5 = metrics.get("z5_seconds") or 0
    total_zone_time = z1 + z2 + z3 + z4 + z5

    # Avoid division by zero — if no HR zone data, fall back to easy
    if total_zone_time <= 0:
        return "easy"

    pct_z1 = z1 / total_zone_time
    pct_z2 = z2 / total_zone_time
    pct_z3 = z3 / total_zone_time
    pct_z4 = z4 / total_zone_time
    pct_z5 = z5 / total_zone_time

    pacing_cv = metrics.get("pacing_cv_pct") or 0
    moving_time = activity.get("moving_time") or 0
    distance = activity.get("distance") or 0
    name = activity.get("name") or ""

    # 1. Race — name matches race keywords AND high intensity
    if _RACE_KEYWORDS.search(name) and (pct_z4 + pct_z5) > 0.30:
        return "race"

    # 2. VO2max intervals — high pace variability AND significant Z5 time
    if pacing_cv > 12 and pct_z5 > 0.15:
        return "vo2max_intervals"

    # 3. Threshold — dominant Z4 work (sustained hard effort)
    if pct_z4 > 0.40:
        return "threshold"

    # 4. Tempo — dominant Z3 work
    if pct_z3 > 0.40:
        return "tempo"

    # 5. Long run — significantly longer/farther than average
    if recent_avg_duration > 0 and recent_avg_distance > 0:
        if moving_time > 1.3 * recent_avg_duration and distance > 1.3 * recent_avg_distance:
            return "long_run"

    # 6. Moderate — mixed aerobic, harder than easy but not tempo
    if (pct_z2 + pct_z3) > 0.70 and pct_z3 < 0.40 and pct_z2 < 0.70:
        return "moderate"

    # 7. Recovery — short and very easy
    if recent_avg_duration > 0:
        if moving_time < 0.6 * recent_avg_duration and (pct_z1 + pct_z2) > 0.85:
            return "recovery"

    # 8. Easy — default
    if (pct_z1 + pct_z2) > 0.70:
        return "easy"

    # Fallback for ambiguous cases
    return "moderate"


# ── Per-activity VDOT ────────────────────────────────────────────────────────

def _jack_daniels_vdot(speed_m_per_min: float, duration_min: float) -> float:
    """
    Jack Daniels VDOT estimate from a performance.
    Duplicated from fitness_engine.py to keep this module dependency-free.
    """
    v = speed_m_per_min
    t = duration_min
    if t <= 0 or v <= 0:
        return 0.0
    numerator = -4.6 + 0.182258 * v + 0.000104 * v ** 2
    denominator = (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t)
        + 0.2989558 * math.exp(-0.1932605 * t)
    )
    if denominator == 0:
        return 0.0
    return numerator / denominator


def compute_per_activity_vdot(distance_m: float, moving_time_s: float) -> float | None:
    """
    Compute VDOT for a single activity. Returns None if distance < 3 km
    or data is invalid.
    """
    if not distance_m or not moving_time_s or distance_m < 3000 or moving_time_s <= 0:
        return None
    speed_m_per_min = (distance_m / moving_time_s) * 60.0
    duration_min = moving_time_s / 60.0
    vdot = _jack_daniels_vdot(speed_m_per_min, duration_min)
    return round(vdot, 2) if vdot > 0 else None

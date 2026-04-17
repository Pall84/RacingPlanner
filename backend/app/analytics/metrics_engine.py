"""
Per-activity metrics engine.

Receives raw Strava stream arrays and computes all running metrics.
All computations are pure Python/numpy — no DB access here.
"""
import math
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Karvonen HR zone boundaries (% of heart rate reserve)
# ---------------------------------------------------------------------------
KARVONEN_ZONES = [
    (0.50, 0.60),  # Z1
    (0.60, 0.70),  # Z2
    (0.70, 0.80),  # Z3
    (0.80, 0.90),  # Z4
    (0.90, 1.00),  # Z5
]

PERCENT_MAX_ZONES = [
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 1.00),
]

# Banister exponential coefficients
TRIMP_COEFF = {"male": 1.92, "female": 1.67}


def _safe_mean(arr) -> float | None:
    if arr is None or len(arr) == 0:
        return None
    return float(np.mean(arr))


def _safe_std(arr) -> float | None:
    if arr is None or len(arr) < 2:
        return None
    return float(np.std(arr, ddof=1))


def _cv_pct(arr) -> float | None:
    m = _safe_mean(arr)
    s = _safe_std(arr)
    if m is None or s is None or m == 0:
        return None
    return float(s / m * 100)


# ---------------------------------------------------------------------------
# Grade Adjusted Pace — Minetti et al. 2002
# ---------------------------------------------------------------------------
def _minetti_cost(grade_frac: float) -> float:
    """
    Energy cost of running on grade (J/kg/m).
    grade_frac: slope as decimal fraction (0.1 = 10% grade).
    Clamp to ±0.45 (±45%).
    """
    g = max(-0.45, min(0.45, grade_frac))
    return (
        155.4 * g**5
        - 30.4 * g**4
        - 43.3 * g**3
        + 46.3 * g**2
        + 19.5 * g
        + 3.6
    )


FLAT_COST = 3.6  # J/kg/m on flat


def compute_gap_speeds(velocity: np.ndarray, grade_pct: np.ndarray) -> np.ndarray:
    """
    Returns grade-adjusted speeds (m/s) for each sample.
    grade_pct: grade in percent (5.0 = 5% uphill).
    """
    grade_frac = grade_pct / 100.0
    costs = np.vectorize(_minetti_cost)(grade_frac)
    costs = np.where(costs <= 0, FLAT_COST, costs)  # safety guard
    return velocity * (FLAT_COST / costs)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------
@dataclass
class MetricsResult:
    avg_pace_sec_per_km: float | None = None
    best_pace_sec_per_km: float | None = None
    avg_gap_sec_per_km: float | None = None

    ef_first_half: float | None = None
    ef_second_half: float | None = None
    pace_decoupling_pct: float | None = None

    cadence_avg: float | None = None
    cadence_min: float | None = None
    cadence_max: float | None = None
    cadence_cv_pct: float | None = None

    stride_length_avg_m: float | None = None
    stride_length_cv_pct: float | None = None

    z1_seconds: int = 0
    z2_seconds: int = 0
    z3_seconds: int = 0
    z4_seconds: int = 0
    z5_seconds: int = 0

    trimp_total: float = 0.0
    trimp_z1: float = 0.0
    trimp_z2: float = 0.0
    trimp_z3: float = 0.0
    trimp_z4: float = 0.0
    trimp_z5: float = 0.0

    rss: float = 0.0

    normalized_power: float | None = None
    intensity_factor: float | None = None

    pacing_cv_pct: float | None = None
    moving_elapsed_ratio: float | None = None
    total_elevation_loss: float | None = None

    km_splits: list = field(default_factory=list)
    gap_speeds: np.ndarray | None = None  # not stored in DB, used internally


class ActivityMetricsEngine:
    def __init__(
        self,
        activity: dict,
        streams: dict,
        max_hr: int = 190,
        resting_hr: int = 50,
        ftp_watts: float = 250.0,
        hr_zone_method: str = "karvonen",
        trimp_gender: str = "male",
    ):
        self.activity = activity
        self.max_hr = max_hr
        self.resting_hr = resting_hr
        self.ftp_watts = ftp_watts
        self.hr_zone_method = hr_zone_method
        self.trimp_gender = trimp_gender

        # Extract streams (all optional)
        self.time_s = np.array(streams.get("time", []), dtype=float)
        self.distance_m = np.array(streams.get("distance", []), dtype=float)
        self.velocity = np.array(streams.get("velocity_smooth", []), dtype=float)
        self.hr = np.array(streams.get("heartrate", []), dtype=float)
        self.cadence_raw = np.array(streams.get("cadence", []), dtype=float)  # one-foot SPM
        self.altitude = np.array(streams.get("altitude", []), dtype=float)
        self.grade_pct = np.array(streams.get("grade_smooth", []), dtype=float)
        self.watts = np.array(streams.get("watts", []), dtype=float)
        self.moving = np.array(streams.get("moving", []), dtype=bool)

        # Full cadence (SPM) = one-foot × 2
        self.cadence = self.cadence_raw * 2 if len(self.cadence_raw) > 0 else self.cadence_raw

    def _hr_zone_boundaries(self) -> list[tuple[float, float]]:
        zones = KARVONEN_ZONES if self.hr_zone_method == "karvonen" else PERCENT_MAX_ZONES
        hrr = self.max_hr - self.resting_hr
        if self.hr_zone_method == "karvonen":
            return [(self.resting_hr + lo * hrr, self.resting_hr + hi * hrr) for lo, hi in zones]
        else:
            return [(lo * self.max_hr, hi * self.max_hr) for lo, hi in zones]

    def compute_hr_zones_and_trimp(self) -> dict:
        if len(self.hr) == 0 or len(self.time_s) < 2:
            return {}

        zone_bounds = self._hr_zone_boundaries()
        coeff = TRIMP_COEFF.get(self.trimp_gender, 1.92)
        hrr = self.max_hr - self.resting_hr

        z_seconds = [0, 0, 0, 0, 0]
        z_trimp = [0.0, 0.0, 0.0, 0.0, 0.0]

        n = min(len(self.hr), len(self.time_s))
        for i in range(n - 1):
            dt = self.time_s[i + 1] - self.time_s[i]
            if dt <= 0:
                continue
            hr_val = self.hr[i]
            if hr_val <= 0:
                continue

            hr_reserve = (hr_val - self.resting_hr) / hrr
            hr_reserve = max(0.0, min(1.0, hr_reserve))

            # TRIMP Banister
            y = math.exp(coeff * hr_reserve)
            trimp_delta = (dt / 60.0) * hr_reserve * 0.64 * y

            # Assign to zone
            for z_idx, (lo, hi) in enumerate(zone_bounds):
                if lo <= hr_val < hi or (z_idx == 4 and hr_val >= hi):
                    z_seconds[z_idx] += int(dt)
                    z_trimp[z_idx] += trimp_delta
                    break

        return {
            "z1_seconds": z_seconds[0],
            "z2_seconds": z_seconds[1],
            "z3_seconds": z_seconds[2],
            "z4_seconds": z_seconds[3],
            "z5_seconds": z_seconds[4],
            "trimp_z1": z_trimp[0],
            "trimp_z2": z_trimp[1],
            "trimp_z3": z_trimp[2],
            "trimp_z4": z_trimp[3],
            "trimp_z5": z_trimp[4],
            "trimp_total": sum(z_trimp),
        }

    def compute_pace_metrics(self) -> dict:
        result = {}
        moving_time = self.activity.get("moving_time") or 0
        elapsed_time = self.activity.get("elapsed_time") or 0
        distance_m = self.activity.get("distance") or 0

        if distance_m > 0 and moving_time > 0:
            result["avg_pace_sec_per_km"] = moving_time / (distance_m / 1000.0)

        if len(self.velocity) > 0:
            # Best pace: 95th percentile of speed (to ignore brief sprints/GPS artifacts)
            fast_speed = float(np.percentile(self.velocity[self.velocity > 0.5], 95)) if np.any(self.velocity > 0.5) else None
            if fast_speed and fast_speed > 0:
                result["best_pace_sec_per_km"] = 1000.0 / fast_speed

        if elapsed_time > 0 and moving_time > 0:
            result["moving_elapsed_ratio"] = moving_time / elapsed_time

        return result

    def compute_gap(self) -> dict:
        if len(self.velocity) == 0 or len(self.grade_pct) == 0:
            return {}

        n = min(len(self.velocity), len(self.grade_pct))
        vel = self.velocity[:n]
        grade = self.grade_pct[:n]

        gap_speeds = compute_gap_speeds(vel, grade)
        self._gap_speeds = gap_speeds  # cache for km splits

        moving_mask = vel > 0.5
        if np.sum(moving_mask) == 0:
            return {}

        avg_gap_speed = float(np.mean(gap_speeds[moving_mask]))
        if avg_gap_speed > 0:
            return {"avg_gap_sec_per_km": 1000.0 / avg_gap_speed}
        return {}

    def compute_pace_decoupling(self) -> dict:
        if len(self.velocity) < 10 or len(self.hr) < 10 or len(self.distance_m) < 10:
            return {}

        total_dist = self.distance_m[-1]
        if total_dist <= 0:
            return {}

        mid_dist = total_dist / 2.0
        n = min(len(self.distance_m), len(self.velocity), len(self.hr))
        mid_idx = np.searchsorted(self.distance_m[:n], mid_dist)

        if mid_idx < 5 or mid_idx >= n - 5:
            return {}

        vel_first = self.velocity[:mid_idx]
        hr_first = self.hr[:mid_idx]
        vel_second = self.velocity[mid_idx:n]
        hr_second = self.hr[mid_idx:n]

        mask1 = (vel_first > 0.5) & (hr_first > 0)
        mask2 = (vel_second > 0.5) & (hr_second > 0)

        if np.sum(mask1) < 5 or np.sum(mask2) < 5:
            return {}

        ef1 = float(np.mean(vel_first[mask1])) / float(np.mean(hr_first[mask1]))
        ef2 = float(np.mean(vel_second[mask2])) / float(np.mean(hr_second[mask2]))

        if ef1 == 0:
            return {}

        decoupling = (ef1 - ef2) / ef1 * 100.0
        return {
            "ef_first_half": ef1,
            "ef_second_half": ef2,
            "pace_decoupling_pct": decoupling,
        }

    def compute_cadence_metrics(self) -> dict:
        if len(self.cadence) == 0:
            return {}
        # Filter moving only
        if len(self.velocity) == len(self.cadence):
            mask = self.velocity > 0.5
            c = self.cadence[mask] if np.sum(mask) > 0 else self.cadence
        else:
            c = self.cadence
        c = c[c > 0]
        if len(c) == 0:
            return {}

        return {
            "cadence_avg": float(np.mean(c)),
            "cadence_min": float(np.min(c)),
            "cadence_max": float(np.max(c)),
            "cadence_cv_pct": _cv_pct(c),
        }

    def compute_stride_length(self) -> dict:
        if len(self.velocity) == 0 or len(self.cadence) == 0:
            return {}
        n = min(len(self.velocity), len(self.cadence))
        vel = self.velocity[:n]
        cad = self.cadence[:n]  # full SPM

        mask = (vel > 0.5) & (cad > 60)
        if np.sum(mask) < 5:
            return {}

        # stride_length (m) = speed (m/s) / (SPM / 60)
        stride = vel[mask] / (cad[mask] / 60.0)
        return {
            "stride_length_avg_m": float(np.mean(stride)),
            "stride_length_cv_pct": _cv_pct(stride),
        }

    def compute_normalized_power(self) -> dict:
        if len(self.watts) < 30:
            return {}
        w = self.watts.copy()
        w = np.where(w < 0, 0, w)

        # 30-second rolling mean
        window = 30
        kernel = np.ones(window) / window
        rolling = np.convolve(w, kernel, mode="valid")
        np_val = float(np.mean(rolling**4) ** 0.25)
        if_val = np_val / self.ftp_watts if self.ftp_watts > 0 else None

        return {
            "normalized_power": np_val,
            "intensity_factor": if_val,
        }

    def compute_elevation_loss(self) -> dict:
        if len(self.altitude) < 2:
            return {}
        diffs = np.diff(self.altitude)
        loss = float(np.sum(np.abs(diffs[diffs < 0])))
        return {"total_elevation_loss": loss}

    def compute_km_splits(self) -> list[dict]:
        if len(self.distance_m) < 2 or len(self.time_s) < 2:
            return []

        gap_speeds = getattr(self, "_gap_speeds", None)
        n = len(self.distance_m)
        total_dist = self.distance_m[-1]
        if total_dist <= 0:
            return []

        splits = []
        km_idx = 1
        start_i = 0
        target_dist = 1000.0

        while target_dist <= total_dist + 50:  # +50m tolerance for last km
            end_i = np.searchsorted(self.distance_m, target_dist)
            end_i = min(end_i, n - 1)

            if end_i <= start_i:
                break

            seg_dist = self.distance_m[end_i] - self.distance_m[start_i]
            if seg_dist < 100:  # ignore tiny trailing segment
                break

            seg_time = self.time_s[end_i] - self.time_s[start_i]
            pace = (seg_time / (seg_dist / 1000.0)) if seg_dist > 0 else None

            avg_hr = None
            if len(self.hr) >= end_i:
                seg_hr = self.hr[start_i:end_i]
                seg_hr = seg_hr[seg_hr > 0]
                avg_hr = float(np.mean(seg_hr)) if len(seg_hr) > 0 else None

            avg_cad = None
            if len(self.cadence) >= end_i:
                seg_cad = self.cadence[start_i:end_i]
                seg_cad = seg_cad[seg_cad > 0]
                avg_cad = float(np.mean(seg_cad)) if len(seg_cad) > 0 else None

            elev_gain = elev_loss = avg_grade = None
            if len(self.altitude) >= end_i:
                seg_alt = self.altitude[start_i:end_i]
                if len(seg_alt) > 1:
                    diffs = np.diff(seg_alt)
                    elev_gain = float(np.sum(diffs[diffs > 0]))
                    elev_loss = float(np.sum(np.abs(diffs[diffs < 0])))
            if len(self.grade_pct) >= end_i:
                seg_grade = self.grade_pct[start_i:end_i]
                avg_grade = float(np.mean(seg_grade)) if len(seg_grade) > 0 else None

            gap_pace = None
            if gap_speeds is not None and len(gap_speeds) >= end_i:
                seg_gap = gap_speeds[start_i:end_i]
                seg_gap_moving = seg_gap[seg_gap > 0.5] if len(seg_gap) > 0 else seg_gap
                if len(seg_gap_moving) > 0:
                    avg_gap_spd = float(np.mean(seg_gap_moving))
                    gap_pace = 1000.0 / avg_gap_spd if avg_gap_spd > 0 else None

            splits.append({
                "km_index": km_idx,
                "distance_m": seg_dist,
                "duration_sec": seg_time,
                "pace_sec_per_km": pace,
                "gap_sec_per_km": gap_pace,
                "avg_hr": avg_hr,
                "avg_cadence": avg_cad,
                "elevation_gain": elev_gain,
                "elevation_loss": elev_loss,
                "avg_grade_pct": avg_grade,
            })

            km_idx += 1
            start_i = end_i
            target_dist += 1000.0

        return splits

    def compute_pacing_cv(self, km_splits: list[dict]) -> float | None:
        paces = [s["pace_sec_per_km"] for s in km_splits if s.get("pace_sec_per_km")]
        if len(paces) < 2:
            return None
        arr = np.array(paces)
        return _cv_pct(arr)

    def compute_all(self) -> MetricsResult:
        result = MetricsResult()

        pace = self.compute_pace_metrics()
        result.avg_pace_sec_per_km = pace.get("avg_pace_sec_per_km")
        result.best_pace_sec_per_km = pace.get("best_pace_sec_per_km")
        result.moving_elapsed_ratio = pace.get("moving_elapsed_ratio")

        gap = self.compute_gap()
        result.avg_gap_sec_per_km = gap.get("avg_gap_sec_per_km")

        hr_trimp = self.compute_hr_zones_and_trimp()
        result.z1_seconds = hr_trimp.get("z1_seconds", 0)
        result.z2_seconds = hr_trimp.get("z2_seconds", 0)
        result.z3_seconds = hr_trimp.get("z3_seconds", 0)
        result.z4_seconds = hr_trimp.get("z4_seconds", 0)
        result.z5_seconds = hr_trimp.get("z5_seconds", 0)
        result.trimp_z1 = hr_trimp.get("trimp_z1", 0.0)
        result.trimp_z2 = hr_trimp.get("trimp_z2", 0.0)
        result.trimp_z3 = hr_trimp.get("trimp_z3", 0.0)
        result.trimp_z4 = hr_trimp.get("trimp_z4", 0.0)
        result.trimp_z5 = hr_trimp.get("trimp_z5", 0.0)
        result.trimp_total = hr_trimp.get("trimp_total", 0.0)
        result.rss = result.trimp_total

        decoupling = self.compute_pace_decoupling()
        result.ef_first_half = decoupling.get("ef_first_half")
        result.ef_second_half = decoupling.get("ef_second_half")
        result.pace_decoupling_pct = decoupling.get("pace_decoupling_pct")

        cad = self.compute_cadence_metrics()
        result.cadence_avg = cad.get("cadence_avg")
        result.cadence_min = cad.get("cadence_min")
        result.cadence_max = cad.get("cadence_max")
        result.cadence_cv_pct = cad.get("cadence_cv_pct")

        stride = self.compute_stride_length()
        result.stride_length_avg_m = stride.get("stride_length_avg_m")
        result.stride_length_cv_pct = stride.get("stride_length_cv_pct")

        np_data = self.compute_normalized_power()
        result.normalized_power = np_data.get("normalized_power")
        result.intensity_factor = np_data.get("intensity_factor")

        elev = self.compute_elevation_loss()
        result.total_elevation_loss = elev.get("total_elevation_loss")

        result.km_splits = self.compute_km_splits()
        result.pacing_cv_pct = self.compute_pacing_cv(result.km_splits)

        return result

"""
Pure-Python GPX parser.
No external dependencies — uses stdlib xml.etree.ElementTree, math, and bisect.
Elevation gain/loss is computed from raw GPX elevation data (no smoothing).
Course segments use Garmin Pace Pro-style elevation-inflection-point segmentation.
"""
import bisect
import math
import xml.etree.ElementTree as ET

_GPX_NS = {
    "gpx": "http://www.topografix.com/GPX/1/1",
    "gpx10": "http://www.topografix.com/GPX/1/0",
}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two GPS coordinates."""
    R = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _find_trkpts(root: ET.Element):
    """Find all trkpt elements regardless of namespace variant."""
    for ns_uri in _GPX_NS.values():
        pts = root.findall(f".//{{{ns_uri}}}trkpt")
        if pts:
            return pts, ns_uri
    pts = root.findall(".//trkpt")
    return pts, ""


def _raw_gain_loss(eles: list) -> tuple:
    """Sum all positive and negative elevation deltas directly from raw data."""
    gain = loss = 0.0
    for i in range(1, len(eles)):
        delta = eles[i] - eles[i - 1]
        if delta > 0:
            gain += delta
        else:
            loss += abs(delta)
    return gain, loss


# ── Elevation-segment helpers (Garmin Pace Pro style) ────────────────────────

def _smooth_elevation(cum_dist, elevation, window_m=200.0):
    """
    Distance-window moving average over elevation.
    O(n) two-pointer pass. Used ONLY for segment boundary detection —
    raw elevation is always used for reported gain/loss values.
    """
    n = len(cum_dist)
    half = window_m / 2.0
    smoothed = [0.0] * n
    lo = hi = 0
    window_sum = 0.0
    window_count = 0

    for i in range(n):
        center = cum_dist[i]
        # Expand right edge
        while hi < n and cum_dist[hi] <= center + half:
            window_sum += elevation[hi]
            window_count += 1
            hi += 1
        # Shrink left edge
        while lo < hi and cum_dist[lo] < center - half:
            window_sum -= elevation[lo]
            window_count -= 1
            lo += 1
        smoothed[i] = window_sum / window_count if window_count > 0 else elevation[i]

    return smoothed


def _rdp_vertical(points, epsilon):
    """
    Ramer-Douglas-Peucker simplification using vertical elevation deviation.

    `points` is a list of (cum_dist_m, smoothed_ele_m) tuples.
    Uses an iterative (stack-based) implementation to avoid Python recursion
    limits on large tracks (>10 k points).

    Returns sorted list of retained point indices.
    """
    n = len(points)
    if n <= 2:
        return list(range(n))

    kept = [False] * n
    kept[0] = True
    kept[n - 1] = True

    stack = [(0, n - 1)]
    while stack:
        start, end = stack.pop()
        if end - start < 2:
            continue

        d_start, e_start = points[start]
        d_end, e_end = points[end]
        span = d_end - d_start

        max_dev = 0.0
        max_idx = start + 1

        for k in range(start + 1, end):
            if span == 0.0:
                dev = abs(points[k][1] - e_start)
            else:
                t = (points[k][0] - d_start) / span
                interp_ele = e_start + t * (e_end - e_start)
                dev = abs(points[k][1] - interp_ele)
            if dev > max_dev:
                max_dev = dev
                max_idx = k

        if max_dev > epsilon:
            kept[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))

    return [i for i in range(n) if kept[i]]


def _find_epsilon_for_target(points, target_segments, min_seg_m=400.0):
    """
    Binary-search on RDP epsilon to hit approximately `target_segments`.
    Inflates the target by 12% to account for the subsequent merge pass.
    Converges in ~17 of the 60 allowed iterations.
    """
    rdp_target = int(target_segments * 1.12) + 1
    lo, hi, best_eps = 0.5, 200.0, 200.0

    for _ in range(60):
        mid = (lo + hi) / 2.0
        seg_count = len(_rdp_vertical(points, mid)) - 1
        if seg_count <= rdp_target:
            best_eps = mid
            hi = mid
        else:
            lo = mid
        if hi - lo < 0.001:
            break

    return best_eps


def _merge_short_segments(boundary_indices, cum_dist, min_seg_m=400.0):
    """
    Remove boundaries that would create segments shorter than `min_seg_m`.
    Iterates until stable. Always preserves the first and last boundary.
    """
    indices = list(boundary_indices)
    changed = True
    while changed:
        changed = False
        new_indices = [indices[0]]
        i = 1
        while i < len(indices) - 1:
            seg_len = cum_dist[indices[i]] - cum_dist[new_indices[-1]]
            next_seg_len = cum_dist[indices[i + 1]] - cum_dist[indices[i]]
            if seg_len < min_seg_m or next_seg_len < min_seg_m:
                # Skip this boundary — merge its segment with the next
                changed = True
            else:
                new_indices.append(indices[i])
            i += 1
        new_indices.append(indices[-1])
        indices = new_indices

    return indices


def _compute_segment_stats(boundary_indices, cum_dist, filled_ele, has_ele, smoothed_ele=None):
    """
    Compute per-segment statistics.

    - gain/loss: raw elevation (every GPS sample) — accurate total climb/descent
    - grade/net: smoothed elevation at boundary points — stable, noise-free
      (boundaries were chosen from smoothed data so it's consistent to use the
      same values at those edges; a single noisy raw point at a boundary can
      swing a short segment's grade by several percent)

    smoothed_ele is optional; falls back to filled_ele when not provided
    (e.g. the equal-distance fallback where no smoothing was done).
    """
    grade_ele = smoothed_ele if smoothed_ele is not None else filled_ele

    segments = []
    for seg_num, (b_start, b_end) in enumerate(
        zip(boundary_indices, boundary_indices[1:]), start=1
    ):
        seg_dist = cum_dist[b_end] - cum_dist[b_start]
        seg_eles_raw = filled_ele[b_start: b_end + 1]

        if has_ele and len(seg_eles_raw) >= 2:
            seg_gain, seg_loss = _raw_gain_loss(seg_eles_raw)
            elevation_span = grade_ele[b_end] - grade_ele[b_start]  # smoothed endpoints
        else:
            seg_gain = seg_loss = elevation_span = 0.0

        avg_grade = (elevation_span / seg_dist * 100) if seg_dist > 0 else 0.0

        segments.append({
            "km_index": seg_num,
            "distance_m": round(seg_dist, 1),
            "elevation_gain": round(seg_gain, 1),
            "elevation_loss": round(seg_loss, 1),
            "elevation_net": round(elevation_span, 1),   # net change = grade × distance
            "avg_grade_pct": round(avg_grade, 2),
        })

    return segments


def _equal_distance_segments(cum_dist, filled_ele, has_ele, target_segments):
    """
    Fallback segmentation: equal-distance splits.
    Used when the course is flat (RDP produces too few segments) or has no
    elevation data. Uses bisect for efficient boundary snapping.
    """
    total_dist = cum_dist[-1]
    seg_len = total_dist / target_segments
    boundary_indices = [0]

    for s in range(1, target_segments):
        target_d = s * seg_len
        idx = bisect.bisect_left(cum_dist, target_d)
        idx = min(max(idx, boundary_indices[-1] + 1), len(cum_dist) - 1)
        if idx not in boundary_indices:
            boundary_indices.append(idx)

    if boundary_indices[-1] != len(cum_dist) - 1:
        boundary_indices.append(len(cum_dist) - 1)

    return _compute_segment_stats(boundary_indices, cum_dist, filled_ele, has_ele)


def _elevation_segments(
    cum_dist,
    filled_ele,
    has_ele,
    target_segments=74,
    min_seg_m=400.0,
    smooth_window_m=200.0,
):
    """
    Build Garmin Pace Pro-style elevation-inflection-point segments.

    Algorithm:
      1. Smooth elevation with a 200 m distance-window moving average
      2. Run iterative RDP (vertical deviation metric) with binary-searched epsilon
      3. Merge any sub-400 m segments
      4. Compute stats from raw (unsmoothed) elevation

    Falls back to equal-distance segments on flat/no-elevation courses.
    """
    total_dist = cum_dist[-1]

    # Too short for meaningful segmentation
    if total_dist < min_seg_m * 2:
        return _compute_segment_stats([0, len(cum_dist) - 1], cum_dist, filled_ele, has_ele)

    # No elevation data → equal-distance fallback
    if not has_ele:
        return _equal_distance_segments(cum_dist, filled_ele, has_ele, target_segments)

    # Build smoothed elevation for boundary detection
    smoothed = _smooth_elevation(cum_dist, filled_ele, window_m=smooth_window_m)
    rdp_points = list(zip(cum_dist, smoothed))

    # Find epsilon that targets slightly more segments than needed (merge pass trims)
    epsilon = _find_epsilon_for_target(rdp_points, target_segments, min_seg_m)
    retained_indices = _rdp_vertical(rdp_points, epsilon)

    # Merge short segments
    merged_indices = _merge_short_segments(retained_indices, cum_dist, min_seg_m)

    # Flat-course fallback: RDP collapsed to too few segments
    if len(merged_indices) - 1 < target_segments // 3:
        return _equal_distance_segments(cum_dist, filled_ele, has_ele, target_segments)

    # Compute stats: raw elevation for gain/loss, smoothed for grade/net at boundaries
    return _compute_segment_stats(merged_indices, cum_dist, filled_ele, has_ele, smoothed_ele=smoothed)


# ── Waypoint parser ──────────────────────────────────────────────────────────

def _parse_waypoints(root: ET.Element, ns_uri: str, raw_lat: list, raw_lon: list, cum_dist: list) -> list:
    """
    Extract <wpt> elements and project each onto the nearest track point.

    Uses squared lat/lon difference (no trig) for fast nearest-point lookup —
    accurate enough for this purpose since all points are in the same small region.

    Filters out start/finish markers by inspecting the <cmt>/<desc> text and by
    proximity to the course endpoints (< 500 m from start or finish).

    Returns list of {name, distance_km, notes} sorted by distance_km.
    """
    # Find wpt elements (try namespaced first, then bare)
    wpts = root.findall(f"{{{ns_uri}}}wpt") if ns_uri else []
    if not wpts:
        wpts = root.findall("wpt")
    if not wpts:
        return []

    def _txt(el: ET.Element, tag: str) -> str:
        child = el.find(f"{{{ns_uri}}}{tag}") if ns_uri else None
        if child is None:
            child = el.find(tag)
        return (child.text or "").strip() if child is not None else ""

    total_dist = cum_dist[-1]
    stations = []

    for wpt in wpts:
        try:
            wlat = float(wpt.get("lat"))
            wlon = float(wpt.get("lon"))
        except (TypeError, ValueError):
            continue

        name = _txt(wpt, "name")
        if not name:
            continue

        notes = _txt(wpt, "cmt") or _txt(wpt, "desc")

        # Skip start / finish markers
        notes_lower = notes.lower()
        if notes_lower in ("start", "finish") or notes_lower.startswith("start") or notes_lower.startswith("finish"):
            continue

        # Nearest track-point search using cos(lat)-corrected squared distance.
        # Raw lat/lon diff² is biased at high latitudes where 1° lon ≪ 1° lat
        # in ground distance. Applying cos(lat) to the lon diff fixes this.
        import math
        cos_lat = math.cos(math.radians(wlat))
        nearest_idx = min(
            range(len(raw_lat)),
            key=lambda i: (raw_lat[i] - wlat) ** 2 + ((raw_lon[i] - wlon) * cos_lat) ** 2,
        )
        dist_m = cum_dist[nearest_idx]

        # Skip waypoints within 500 m of the start or finish (likely start/finish flags)
        if dist_m < 500 or dist_m > total_dist - 500:
            continue

        stations.append({
            "name": name,
            "distance_km": round(dist_m / 1000, 2),
            "lat": wlat,
            "lon": wlon,
            "notes": notes,
        })

    stations.sort(key=lambda s: s["distance_km"])
    return stations


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_gpx(gpx_xml: str) -> dict:
    """
    Parse a GPX track and return course data suitable for storage and analysis.

    Returns:
        {
          "distance_m": float,
          "total_elevation_gain": float,
          "total_elevation_loss": float,
          "elevation_profile": [[dist_m, ele_m], ...],   # ≤500 points
          "latlng": [[lat, lon], ...],                   # ≤1000 points
          "km_splits": [
              {
                "km_index": int,          # 1-based segment number
                "distance_m": float,      # variable length (elevation-based)
                "elevation_gain": float,
                "elevation_loss": float,
                "avg_grade_pct": float,
              }, ...
          ],
        }

    Raises ValueError if no track points found.
    """
    root = ET.fromstring(gpx_xml)
    pts, ns_uri = _find_trkpts(root)

    if not pts:
        raise ValueError("No track points found in GPX file")

    # ── Extract raw lat/lon/ele ───────────────────────────────────────────────
    raw_lat = []
    raw_lon = []
    raw_ele = []

    for pt in pts:
        try:
            lat = float(pt.get("lat"))
            lon = float(pt.get("lon"))
        except (TypeError, ValueError):
            continue
        ele_el = pt.find(f"{{{ns_uri}}}ele") if ns_uri else pt.find("ele")
        try:
            ele = float(ele_el.text) if ele_el is not None and ele_el.text else None
        except (TypeError, ValueError):
            ele = None
        raw_lat.append(lat)
        raw_lon.append(lon)
        raw_ele.append(ele)

    n = len(raw_lat)
    if n < 2:
        raise ValueError("GPX file has fewer than 2 valid track points")

    # ── Compute cumulative distance ───────────────────────────────────────────
    cum_dist = [0.0]
    for i in range(1, n):
        d = _haversine_m(raw_lat[i - 1], raw_lon[i - 1], raw_lat[i], raw_lon[i])
        cum_dist.append(cum_dist[-1] + d)

    total_dist = cum_dist[-1]

    # ── Fill any missing elevation values via linear interpolation ────────────
    ele_available = [e for e in raw_ele if e is not None]
    has_ele = len(ele_available) >= n * 0.5

    if has_ele:
        filled_ele = list(raw_ele)
        for i in range(n):
            if filled_ele[i] is None:
                prev_i = next((j for j in range(i - 1, -1, -1) if filled_ele[j] is not None), None)
                next_i = next((j for j in range(i + 1, n) if filled_ele[j] is not None), None)
                if prev_i is not None and next_i is not None:
                    t = (i - prev_i) / (next_i - prev_i)
                    filled_ele[i] = filled_ele[prev_i] + t * (filled_ele[next_i] - filled_ele[prev_i])
                elif prev_i is not None:
                    filled_ele[i] = filled_ele[prev_i]
                elif next_i is not None:
                    filled_ele[i] = filled_ele[next_i]
                else:
                    filled_ele[i] = 0.0
    else:
        filled_ele = [0.0] * n

    # ── Total elevation gain/loss — raw deltas, no smoothing ─────────────────
    total_gain, total_loss = _raw_gain_loss(filled_ele) if has_ele else (0.0, 0.0)

    # ── Downsample for elevation profile (≤500 pts) ──────────────────────────
    step = max(1, n // 500)
    elevation_profile = [
        [round(cum_dist[i], 1), round(filled_ele[i], 1)]
        for i in range(0, n, step)
    ]
    if elevation_profile[-1][0] < round(total_dist, 1):
        elevation_profile.append([round(total_dist, 1), round(filled_ele[-1], 1)])

    # ── Downsample latlng for map (≤1000 pts) ────────────────────────────────
    step_ll = max(1, n // 1000)
    latlng_indices = list(range(0, n, step_ll))
    if latlng_indices[-1] != n - 1:
        latlng_indices.append(n - 1)
    latlng = [
        [round(raw_lat[i], 6), round(raw_lon[i], 6)]
        for i in latlng_indices
    ]
    # Store cumulative distances at each latlng point (from full-resolution track)
    # so the map can position markers by distance without recomputing haversine
    latlng_cum_dist = [round(cum_dist[i], 1) for i in latlng_indices]

    # ── Build elevation-based course segments (Garmin Pace Pro style) ─────────
    km_splits = _elevation_segments(
        cum_dist=cum_dist,
        filled_ele=filled_ele,
        has_ele=has_ele,
        target_segments=74,
        min_seg_m=400.0,
        smooth_window_m=200.0,
    )

    # ── Extract waypoints (aid stations etc.) from <wpt> elements ────────────
    waypoints = _parse_waypoints(root, ns_uri, raw_lat, raw_lon, cum_dist)

    return {
        "distance_m": round(total_dist, 1),
        "total_elevation_gain": round(total_gain, 1),
        "total_elevation_loss": round(total_loss, 1),
        "elevation_profile": elevation_profile,
        "latlng": latlng,
        "latlng_cum_dist": latlng_cum_dist,
        "km_splits": km_splits,
        "waypoints": waypoints,
    }

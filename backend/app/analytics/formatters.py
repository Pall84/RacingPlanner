"""Shared time/pace formatters.

These were duplicated across routes_races.py, race_predictor.py, and
routes_fitness.py with near-identical implementations and subtly
different null handling ("–" vs None). Extract to one place so future
format changes (seconds precision, separator style, etc.) happen once.

The canonical "no value" sentinel is "–" (en-dash) — matches the
frontend's own placeholder and is what the Settings/Dashboard pages
already display for missing data.
"""
from __future__ import annotations


def fmt_time(sec: float | int | None) -> str:
    """Format a duration as ``h:mm:ss`` (≥1h) or ``m:ss``.

    Returns "–" for null/zero/negative input.
    """
    if not sec or sec <= 0:
        return "–"
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_pace(sec_per_km: float | None) -> str:
    """Format a pace in seconds-per-km as ``m:ss /km``.

    Returns "–" for null/zero/negative input.
    """
    if not sec_per_km or sec_per_km <= 0:
        return "–"
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    return f"{m}:{s:02d} /km"

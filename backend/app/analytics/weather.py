"""Race-day weather adjustment.

Heat and humidity have a measurable, predictable effect on endurance race
performance — especially marathons. Typical rule of thumb (Maughan &
Shirreffs; Ely et al. 2007): each 5 °C above ~10 °C costs ~1% of race pace
at marathon distance, with humidity compounding via wet-bulb globe temp.

We pull the forecast from Open-Meteo (free, no API key, no quotas at this
scale) for the race's GPS midpoint on the race date, then apply a simple
linear penalty. Gated to races within 14 days (forecast horizon) and to
races with GPS data.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("racingplanner.weather")

_API_URL = "https://api.open-meteo.com/v1/forecast"


async def fetch_race_weather(
    latitude: float, longitude: float, race_date: str,
) -> dict | None:
    """Fetch the hourly forecast for the race location on the race date.

    Returns a dict of summary stats, or None if the forecast isn't available
    (too far in the future, service down, bad coords, etc.).
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(
                _API_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "start_date": race_date,
                    "end_date": race_date,
                    "hourly": "temperature_2m,relative_humidity_2m",
                    "timezone": "auto",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("weather fetch failed for %s,%s on %s: %s",
                    latitude, longitude, race_date, e)
        return None

    hourly = data.get("hourly") or {}
    temps = hourly.get("temperature_2m") or []
    rhs = hourly.get("relative_humidity_2m") or []
    if not temps or not rhs:
        return None

    # Race window: 07:00–14:00 local covers the vast majority of race start
    # times (early morning marathon → midday finish). If fewer hours were
    # returned, use what we have.
    n = len(temps)
    start_hr = min(7, n - 1)
    end_hr = min(14, n)
    race_temps = temps[start_hr:end_hr] or temps
    race_rhs = rhs[start_hr:end_hr] or rhs

    # Defensive guard — `or temps` above handles most thin-forecast cases, but
    # if the provider returned hours that are all None (e.g. sparse early data
    # for a date far in the future), the filtered slice can still be empty.
    # Dividing by zero below would crash the predictor; fail closed instead.
    if not race_temps or not race_rhs:
        return None

    avg_temp = sum(race_temps) / len(race_temps)
    max_temp = max(race_temps)
    avg_rh = sum(race_rhs) / len(race_rhs)

    return {
        "avg_temp_c": round(avg_temp, 1),
        "max_temp_c": round(max_temp, 1),
        "avg_humidity_pct": round(avg_rh, 0),
        "hourly_count": len(race_temps),
    }


def weather_pace_penalty(weather: dict | None) -> tuple[float, dict | None]:
    """Translate the weather summary into a pace-multiplier factor.

    Model (Maughan & Shirreffs / Ely 2007, simplified):
      T_effective = avg_temp + humidity_boost
        humidity_boost = max(0, (RH − 60) / 20)  # ~+2 °C effective at 100% RH
      penalty = max(0, (T_effective − 10) / 5) × 0.01   # +1% per 5 °C
      clamp penalty ∈ [0, 0.05]  (max +5% — don't let a 35 °C forecast
      produce a 12% penalty that swamps the prediction)

    We use avg_temp (race window) rather than max — a race finishing before
    noon doesn't feel the 2pm peak.
    """
    if not weather:
        return 1.0, None

    t = weather.get("avg_temp_c")
    rh = weather.get("avg_humidity_pct")
    if t is None:
        return 1.0, None

    humidity_boost = max(0.0, (rh - 60) / 20.0) if rh is not None else 0.0
    t_effective = t + humidity_boost

    penalty = max(0.0, (t_effective - 10.0) / 5.0) * 0.01
    penalty = min(0.05, penalty)

    return 1.0 + penalty, {
        "avg_temp_c": t,
        "avg_humidity_pct": rh,
        "effective_temp_c": round(t_effective, 1),
        "penalty_pct": round(penalty * 100, 1),
    }


def midpoint_of_latlng(latlng: list) -> tuple[float, float] | None:
    """Return the mean lat/lng from a list of [lat, lon] pairs.

    Good enough for weather since race courses are typically small enough
    geographically that the midpoint's forecast represents the whole course
    within sensor accuracy. Returns None if no valid coords.
    """
    if not latlng:
        return None
    lats = []
    lons = []
    for pair in latlng:
        try:
            lat = float(pair[0])
            lon = float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        # Cheap sanity check — real coords are in [-90, 90] × [-180, 180]
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            lats.append(lat)
            lons.append(lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)

"""Strava HTTP client with a global app-wide cap and a per-athlete fairness cap.

Strava enforces rate limits per OAuth app: 100 / 15 min, 1000 / day (we run
comfortably below with 90 / 900). Those limits are shared across every user
of this backend. To keep a single heavy user from starving the rest, each
athlete gets a fair slice of the 15-min window via a per-user counter.

Single-instance only — on Render we run one container, so in-process counters
are sufficient. If we ever scale horizontally, move these to Redis.
"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

import httpx

BASE_URL = "https://www.strava.com/api/v3"

# App-wide hard caps — stay under Strava's real limits.
APP_LIMIT_15MIN = 90
APP_LIMIT_DAILY = 900

# Per-athlete fair share — tuned so ~3 concurrent syncers can coexist.
PER_ATHLETE_LIMIT_15MIN = 35

_WINDOW_15MIN = 900
_WINDOW_DAILY = 86400

# Deques sorted oldest → newest, prunable by cutoff.
_app_calls: deque[float] = deque()
_per_athlete_calls: dict[int, deque[float]] = defaultdict(deque)


def _prune(dq: deque[float], cutoff: float) -> None:
    while dq and dq[0] < cutoff:
        dq.popleft()


def _check_and_record(athlete_id: int) -> None:
    now = time.time()
    cutoff_15 = now - _WINDOW_15MIN
    cutoff_day = now - _WINDOW_DAILY

    _prune(_app_calls, cutoff_day)
    # app-wide caps
    calls_15 = sum(1 for t in _app_calls if t > cutoff_15)
    if calls_15 >= APP_LIMIT_15MIN:
        raise RuntimeError("Strava app-wide 15-min rate limit reached; try again shortly.")
    if len(_app_calls) >= APP_LIMIT_DAILY:
        raise RuntimeError("Strava app-wide daily rate limit reached; try again tomorrow.")

    # per-user fair share
    user_dq = _per_athlete_calls[athlete_id]
    _prune(user_dq, cutoff_15)
    if len(user_dq) >= PER_ATHLETE_LIMIT_15MIN:
        raise RuntimeError(
            f"Per-user Strava rate limit reached for this athlete "
            f"({PER_ATHLETE_LIMIT_15MIN}/15min). Pause syncing for a few minutes."
        )

    _app_calls.append(now)
    user_dq.append(now)


class StravaClient:
    """Minimal Strava API client with rate-limit gating.

    `athlete_id` is required — it keys the per-user fair-share counter.
    """

    def __init__(self, access_token: str, athlete_id: int):
        self.access_token = access_token
        self.athlete_id = int(athlete_id)
        self._headers = {"Authorization": f"Bearer {access_token}"}

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        _check_and_record(self.athlete_id)

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{BASE_URL}{path}",
                headers=self._headers,
                params=params or {},
            )
            if resp.status_code == 429:
                # Back off briefly, then retry once.
                retry_after_header = resp.headers.get("Retry-After")
                try:
                    delay = int(retry_after_header) if retry_after_header else 30
                except ValueError:
                    delay = 30
                await asyncio.sleep(min(delay, 60))
                return await self._get(path, params)
            resp.raise_for_status()
            return resp.json()

    async def get_athlete(self) -> dict:
        return await self._get("/athlete")

    async def get_activities(
        self,
        page: int = 1,
        per_page: int = 50,
        after: int | None = None,
        before: int | None = None,
    ) -> list[dict]:
        params: dict = {"page": page, "per_page": per_page}
        if after is not None:
            params["after"] = after
        if before is not None:
            params["before"] = before
        result = await self._get("/athlete/activities", params)
        return result if isinstance(result, list) else []

    async def get_activity(self, activity_id: int) -> dict:
        return await self._get(f"/activities/{activity_id}")

    async def get_streams(self, activity_id: int, keys: list[str] | None = None) -> dict:
        if keys is None:
            keys = [
                "time", "distance", "latlng", "altitude", "velocity_smooth",
                "heartrate", "cadence", "watts", "temp", "grade_smooth", "moving",
            ]
        params = {
            "keys": ",".join(keys),
            "key_by_type": "true",
            "resolution": "high",
        }
        result = await self._get(f"/activities/{activity_id}/streams", params)
        return result if isinstance(result, dict) else {}

    async def get_laps(self, activity_id: int) -> list[dict]:
        result = await self._get(f"/activities/{activity_id}/laps")
        return result if isinstance(result, list) else []

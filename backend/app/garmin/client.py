"""Async wrapper around python-garminconnect.

The underlying library is synchronous — every call is dispatched via
`asyncio.to_thread` so it doesn't block the event loop.

Token cache is **per-athlete** (different subdirectories), so one user's
disconnect or token expiry never affects another's.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from garminconnect import Garmin

# /tmp is always writable on containerized hosts; tokens are non-critical
# (library transparently re-authenticates if they're missing/expired).
_TOKEN_ROOT = Path("/tmp/rp_garmin_tokens")


def _token_dir_for(athlete_id: int) -> Path:
    return _TOKEN_ROOT / str(int(athlete_id))


class GarminClient:
    """Async facade over the synchronous Garmin Connect client.

    `athlete_id` is required so each user has an isolated token cache.
    """

    def __init__(self, email: str, password: str, athlete_id: int):
        self._email = email
        self._password = password
        self._athlete_id = int(athlete_id)
        self._client: Garmin | None = None

    @property
    def _tokenstore(self) -> Path:
        return _token_dir_for(self._athlete_id)

    async def login(self) -> None:
        def _do_login():
            client = Garmin(self._email, self._password)
            self._tokenstore.mkdir(parents=True, exist_ok=True)
            client.login(tokenstore=str(self._tokenstore))
            return client

        self._client = await asyncio.to_thread(_do_login)

    def _ensure_client(self) -> Garmin:
        if self._client is None:
            raise RuntimeError("GarminClient not logged in — call login() first")
        return self._client

    async def get_heart_rates(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_heart_rates, date_str)

    async def get_hrv_data(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_hrv_data, date_str)

    async def get_sleep_data(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_sleep_data, date_str)

    async def get_stats(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_stats, date_str)

    async def get_training_readiness(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_training_readiness, date_str)

    async def get_training_status(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_training_status, date_str)

    async def get_body_battery(self, date_str: str) -> list:
        return await asyncio.to_thread(self._ensure_client().get_body_battery, date_str)

    async def get_stress_data(self, date_str: str) -> dict:
        return await asyncio.to_thread(self._ensure_client().get_stress_data, date_str)

    @staticmethod
    def clear_tokens_for(athlete_id: int) -> None:
        """Remove cached tokens for one athlete — used on disconnect."""
        path = _token_dir_for(athlete_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
        token_file = path.with_suffix(".json")
        if token_file.exists():
            token_file.unlink(missing_ok=True)

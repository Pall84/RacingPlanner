"""Strava OAuth — authorization URL, code exchange, token refresh.

Ported from the original backend/strava/auth.py. The module-global `_csrf_state`
is gone: OAuth state is now a signed, stateless token (see app.security) so
concurrent logins and multi-process deployments are safe.
"""
from __future__ import annotations

import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.schema import Athlete

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


def build_authorization_url(*, state: str, redirect_uri: str) -> str:
    """Return the Strava OAuth consent URL.

    `state` must be a signed token from `app.security.sign_oauth_state` so the
    callback can verify it and recover any embedded invite code.
    """
    settings = get_settings()
    params = (
        f"client_id={settings.strava_client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope=activity:read_all"
        f"&state={state}"
    )
    return f"{STRAVA_AUTH_URL}?{params}"


async def exchange_code_for_tokens(code: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            STRAVA_TOKEN_URL,
            data={
                "client_id": settings.strava_client_id,
                "client_secret": settings.strava_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_valid_token(db: AsyncSession, athlete_id: int) -> str:
    """Return a non-expired Strava access token, refreshing if needed."""
    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if athlete is None:
        raise ValueError(f"Athlete {athlete_id} not found")

    if athlete.token_expires - time.time() < 300:
        token_data = await refresh_access_token(athlete.refresh_token)
        athlete.access_token = token_data["access_token"]
        athlete.refresh_token = token_data["refresh_token"]
        athlete.token_expires = token_data["expires_at"]
        await db.commit()

    return athlete.access_token

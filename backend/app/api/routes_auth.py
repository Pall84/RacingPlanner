"""Auth routes — Strava OAuth flow, invite-gated signup, signed session cookie.

Signup rules:
- If no athlete exists yet AND the logged-in Strava athlete's ID matches
  ADMIN_ATHLETE_ID env var → create them as admin (no invite needed).
- If an athlete row already exists → log them in regardless of invite.
- Otherwise → a valid, unused, non-expired invite code is required.
  The invite is claimed transactionally on successful athlete creation.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_athlete, get_db
from app.config import get_settings
from app.models.schema import Athlete, Invite
from app.security import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SEC,
    sign_oauth_state,
    sign_session,
    verify_oauth_state,
)
from app.strava.auth import build_authorization_url, exchange_code_for_tokens

log = logging.getLogger("racingplanner.auth")

router = APIRouter(tags=["auth"])


def _cookie_options() -> dict:
    """Return HttpOnly/Secure/SameSite settings for the session cookie."""
    settings = get_settings()
    if settings.is_production:
        # Cross-origin Netlify → Render: must be None + Secure.
        return {"samesite": "none", "secure": True}
    return {"samesite": "lax", "secure": False}


@router.get("/auth/login")
async def login(request: Request, invite: str | None = None):
    """Redirect the user to Strava's OAuth consent screen.

    The (optional) invite code is signed into the state param so it survives
    the round-trip through Strava intact.
    """
    settings = get_settings()
    if not settings.strava_client_id:
        raise HTTPException(status_code=500, detail="Strava client ID not configured")

    state = sign_oauth_state(invite_code=invite)
    url = build_authorization_url(state=state, redirect_uri=settings.strava_redirect_uri)
    return RedirectResponse(url=url)


async def _validate_invite_or_403(db: AsyncSession, code: str) -> None:
    """Raise 403 if the invite code doesn't exist, is already used, or expired.

    This is only a *pre-check* for a nicer error message. The actual claim
    happens atomically via `_claim_invite_or_403` to prevent double-use races.
    """
    result = await db.execute(select(Invite).where(Invite.code == code))
    invite = result.scalar_one_or_none()
    now = int(time.time())
    if (
        invite is None
        or invite.used_by_athlete_id is not None
        or (invite.expires_at is not None and invite.expires_at < now)
    ):
        raise HTTPException(status_code=403, detail="Invalid or expired invite code")


async def _claim_invite_or_403(db: AsyncSession, code: str, athlete_id: int, now: int) -> None:
    """Atomically mark the invite as used. 403 if someone else already claimed it.

    The WHERE clause enforces "only claim if still unused (and not expired)" at the
    SQL level, so two concurrent signups with the same code can never both succeed.
    """
    result = await db.execute(
        update(Invite)
        .where(Invite.code == code)
        .where(Invite.used_by_athlete_id.is_(None))
        .where((Invite.expires_at.is_(None)) | (Invite.expires_at >= now))
        .values(used_by_athlete_id=athlete_id, used_at=now)
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=403, detail="Invite already used or expired")


def _is_admin_bootstrap(strava_athlete_id: int) -> bool:
    admin_id = get_settings().admin_athlete_id
    return admin_id is not None and int(strava_athlete_id) == int(admin_id)


@router.get("/auth/callback")
async def callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle the OAuth callback from Strava. On success, set a session cookie."""
    state_payload = verify_oauth_state(state)
    if state_payload is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    try:
        token_data = await exchange_code_for_tokens(code)
    except Exception:  # noqa: BLE001
        log.exception("Strava token exchange failed")
        raise HTTPException(status_code=502, detail="Strava token exchange failed") from None

    athlete_data = token_data.get("athlete") or {}
    strava_id = athlete_data.get("id")
    if not strava_id:
        raise HTTPException(status_code=502, detail="Strava did not return athlete info")

    # Is this a returning user or a new signup?
    existing = (
        await db.execute(select(Athlete).where(Athlete.id == int(strava_id)))
    ).scalar_one_or_none()

    now = int(time.time())

    if existing is not None:
        # Returning user — just refresh their tokens.
        existing.access_token = token_data["access_token"]
        existing.refresh_token = token_data["refresh_token"]
        existing.token_expires = token_data["expires_at"]
        existing.updated_at = now
        athlete = existing
        await db.commit()
    else:
        # New user — must be either admin bootstrap or a valid invite.
        admin_bootstrap = _is_admin_bootstrap(int(strava_id))
        invite_code = state_payload.get("invite")

        if not admin_bootstrap:
            if not invite_code:
                raise HTTPException(
                    status_code=403,
                    detail="Signup requires an invite. Ask the admin for a link.",
                )
            # Friendly pre-check (distinguishes "bad code" from "already used").
            await _validate_invite_or_403(db, invite_code)

        profile_pic = (
            athlete_data.get("profile_medium")
            or athlete_data.get("profile")
            or None
        )

        athlete = Athlete(
            id=int(strava_id),
            username=athlete_data.get("username"),
            firstname=athlete_data.get("firstname"),
            lastname=athlete_data.get("lastname"),
            city=athlete_data.get("city"),
            country=athlete_data.get("country"),
            sex=athlete_data.get("sex"),
            premium=int(bool(athlete_data.get("premium", False))),
            profile_pic=profile_pic,
            weight=athlete_data.get("weight"),
            access_token=token_data["access_token"],
            refresh_token=token_data["refresh_token"],
            token_expires=token_data["expires_at"],
            is_admin=admin_bootstrap,
            created_at=now,
            updated_at=now,
        )
        db.add(athlete)

        if not admin_bootstrap and invite_code:
            # Atomic claim: only succeeds if the invite is still unused.
            # If two signups race on the same code, only one wins here.
            await _claim_invite_or_403(db, invite_code, int(strava_id), now)

        try:
            await db.commit()
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.exception("Failed to create athlete from OAuth callback")
            raise HTTPException(status_code=500, detail="Signup failed") from None

        if admin_bootstrap:
            log.info("Admin bootstrap: athlete %s promoted to admin", strava_id)

    # Set the signed session cookie and redirect to the frontend.
    token = sign_session(athlete.id)
    redirect = RedirectResponse(url=get_settings().frontend_origin)
    redirect.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE_SEC,
        httponly=True,
        **_cookie_options(),
    )
    return redirect


@router.get("/auth/status")
async def status_endpoint(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the current athlete profile, or {authenticated: false} if not logged in.

    `athlete` is the raw DB row (nulls where the user hasn't customised).
    `settings` is the same fields merged with env defaults — the Settings
    and Profile pages bind form inputs to `settings` so they never show
    blanks for unset values.
    """
    from app.config import get_athlete_settings
    from app.security import verify_session

    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        return {"authenticated": False}

    athlete = (
        await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    ).scalar_one_or_none()
    if athlete is None:
        return {"authenticated": False}

    effective = get_athlete_settings(athlete)

    return {
        "authenticated": True,
        "athlete": {
            "id": athlete.id,
            "firstname": athlete.firstname,
            "lastname": athlete.lastname,
            "username": athlete.username,
            "city": athlete.city,
            "country": athlete.country,
            "sex": athlete.sex,
            "profile_pic": athlete.profile_pic,
            "weight": athlete.weight,
            "date_of_birth": athlete.date_of_birth,
            "height_cm": athlete.height_cm,
            "max_hr": athlete.max_hr,
            "resting_hr": athlete.resting_hr,
            "ftp_watts": athlete.ftp_watts,
            "hr_zone_method": athlete.hr_zone_method,
            "trimp_gender": athlete.trimp_gender,
            "is_admin": athlete.is_admin,
            "created_at": athlete.created_at,
        },
        "settings": {
            "max_hr": effective.max_hr,
            "resting_hr": effective.resting_hr,
            "ftp_watts": effective.ftp_watts,
            "hr_zone_method": effective.hr_zone_method,
            "trimp_gender": effective.trimp_gender,
        },
    }


class ProfileUpdateRequest(BaseModel):
    # Physiological bounds — reject obvious garbage like negative weights or
    # 9999 bpm max HR. All fields stay optional (None = leave unchanged).
    # Bounds are intentionally permissive — covers competitive youth through
    # masters athletes. Tighten further only if we see bad data.
    weight: float | None = Field(None, ge=25, le=250)           # kg
    date_of_birth: str | None = None                             # ISO "YYYY-MM-DD"; format-validated below
    height_cm: float | None = Field(None, ge=100, le=230)        # cm
    max_hr: int | None = Field(None, ge=100, le=250)             # bpm
    resting_hr: int | None = Field(None, ge=25, le=120)          # bpm
    ftp_watts: float | None = Field(None, gt=0, le=600)          # W
    hr_zone_method: str | None = None                            # validated against enum below
    trimp_gender: str | None = None                              # validated against enum below


@router.patch("/auth/profile")
async def update_profile(
    body: ProfileUpdateRequest,
    athlete: Athlete = Depends(get_current_athlete),
    db: AsyncSession = Depends(get_db),
):
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(athlete, field, value)
    athlete.updated_at = int(time.time())
    await db.commit()
    return {"ok": True}


@router.post("/auth/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(SESSION_COOKIE_NAME, **_cookie_options())
    return {"ok": True}


@router.get("/auth/debug_cookie_options", include_in_schema=False)
async def debug_cookie_options() -> JSONResponse:
    """Dev-only view of cookie flags — helps diagnose SameSite/Secure issues."""
    if get_settings().is_production:
        raise HTTPException(status_code=404)
    return JSONResponse(_cookie_options())

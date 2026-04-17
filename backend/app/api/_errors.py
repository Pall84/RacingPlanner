"""Exception-to-HTTP translation helpers.

Routes that call external APIs or heavy analytics benefit from turning
specific exceptions into specific HTTP status codes (rather than a generic
500). Use the helpers here to keep the mapping consistent across routes.

The global exception handler in `app.main` is the safety net — any error
not handled here still comes back as a proper JSON 500 with CORS headers.
This module is for giving better UX *on top of* that baseline.
"""
from __future__ import annotations

import logging
from xml.etree.ElementTree import ParseError as XMLParseError

import httpx
from fastapi import HTTPException

log = logging.getLogger("racingplanner.errors")


def translate_strava_error(e: Exception, action: str = "call Strava") -> HTTPException:
    """Map a Strava-call exception to an HTTPException with useful detail.

    - RuntimeError from our rate limiter        → 429
    - httpx.HTTPStatusError 401                 → 401 (tell user to re-login)
    - httpx.HTTPStatusError 404                 → 404 (activity missing upstream)
    - Other httpx.HTTPStatusError               → 502 with Strava's status + body
    - httpx.RequestError (network/timeout)       → 502 (transient)
    - Anything else                              → 500
    """
    # Our rate limiter raises RuntimeError with "rate limit" in the message.
    if isinstance(e, RuntimeError) and "rate limit" in str(e).lower():
        return HTTPException(status_code=429, detail=str(e))

    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        body_hint = (e.response.text or "")[:200]
        if status == 401:
            return HTTPException(
                status_code=401,
                detail="Strava token is no longer valid. Please log out and log back in.",
            )
        if status == 404:
            return HTTPException(
                status_code=404,
                detail="Resource not found on Strava (may have been deleted).",
            )
        return HTTPException(
            status_code=502,
            detail=f"Strava API returned {status}: {body_hint}",
        )

    if isinstance(e, httpx.RequestError):
        return HTTPException(
            status_code=502,
            detail=f"Network error reaching Strava: {type(e).__name__}",
        )

    return HTTPException(
        status_code=500,
        detail=f"Failed to {action}: {type(e).__name__}: {e}",
    )


def translate_garmin_error(e: Exception, action: str = "sync Garmin") -> HTTPException:
    """Map a Garmin-related exception to an HTTPException with useful detail.

    python-garminconnect raises domain-specific errors whose class names are
    self-describing (GarminConnectAuthenticationError, etc.) — we surface the
    class name in the message so the user / logs can distinguish them.
    """
    name = type(e).__name__
    msg = str(e) or "(no message)"
    if "Authentication" in name:
        return HTTPException(
            status_code=401,
            detail=f"Garmin authentication failed ({name}). Disconnect and reconnect with fresh credentials.",
        )
    if "TooManyRequests" in name or "RateLimit" in name:
        return HTTPException(
            status_code=429,
            detail=f"Garmin rate-limited us ({name}). Try again in a few minutes.",
        )
    if "Connection" in name:
        return HTTPException(
            status_code=502,
            detail=f"Could not reach Garmin ({name}).",
        )
    return HTTPException(
        status_code=500,
        detail=f"Failed to {action}: {name}: {msg[:300]}",
    )


def translate_gpx_error(e: Exception) -> HTTPException:
    """Map GPX-parsing / file-upload errors to a 400 (it's user input)."""
    if isinstance(e, XMLParseError):
        return HTTPException(
            status_code=400,
            detail=f"GPX file is not valid XML: {e}",
        )
    # Most validation errors in parse_gpx raise ValueError.
    if isinstance(e, (ValueError, KeyError)):
        return HTTPException(
            status_code=400,
            detail=f"GPX file could not be parsed: {e}",
        )
    # Anything else is a server bug, not user error — let it 500 via the
    # global handler so the stack trace is logged.
    raise e  # noqa: TRY201  (re-raise intentional; caller will let it propagate)

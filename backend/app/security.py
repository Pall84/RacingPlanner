"""Cryptographic helpers for session cookies and OAuth state.

Uses itsdangerous for signed, time-limited, stateless tokens. No server-side
session store required — the signature proves authenticity, the embedded
timestamp proves freshness.
"""
from __future__ import annotations

import secrets
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import get_settings

# --- namespaces (salt values) keep session and state tokens non-interchangeable
_SESSION_SALT = "rp.session.v1"
_OAUTH_STATE_SALT = "rp.oauth_state.v1"

SESSION_COOKIE_NAME = "rp_session"
SESSION_MAX_AGE_SEC = 60 * 60 * 24 * 30  # 30 days
OAUTH_STATE_MAX_AGE_SEC = 10 * 60  # 10 minutes


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        secret_key=get_settings().session_secret,
        salt=salt,
    )


# ---------- session cookie ----------

def sign_session(athlete_id: int) -> str:
    """Return a signed, url-safe token carrying the athlete_id."""
    payload = {"aid": int(athlete_id)}
    return _serializer(_SESSION_SALT).dumps(payload)


def verify_session(token: str, max_age: int = SESSION_MAX_AGE_SEC) -> int | None:
    """Return the athlete_id if the token is valid and fresh, else None."""
    if not token:
        return None
    try:
        payload = _serializer(_SESSION_SALT).loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    aid = payload.get("aid")
    return int(aid) if isinstance(aid, int) else None


# ---------- OAuth state ----------

def sign_oauth_state(*, invite_code: str | None = None, admin_bootstrap: bool = False) -> str:
    """Build a signed state token embedding any invite code carried across OAuth."""
    payload: dict[str, Any] = {
        "nonce": secrets.token_urlsafe(12),
        "invite": invite_code,
        "admin_bootstrap": bool(admin_bootstrap),
    }
    return _serializer(_OAUTH_STATE_SALT).dumps(payload)


def verify_oauth_state(token: str, max_age: int = OAUTH_STATE_MAX_AGE_SEC) -> dict | None:
    """Return the payload if valid and fresh, else None."""
    if not token:
        return None
    try:
        payload = _serializer(_OAUTH_STATE_SALT).loads(token, max_age=max_age)
    except SignatureExpired:
        return None
    except BadSignature:
        return None
    if not isinstance(payload, dict):
        return None
    return payload

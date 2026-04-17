"""Unit tests for signed session cookies and OAuth state tokens."""
from __future__ import annotations

import time

from app import security


def test_session_roundtrip():
    token = security.sign_session(12345)
    assert security.verify_session(token) == 12345


def test_session_rejects_empty_and_none():
    assert security.verify_session("") is None
    assert security.verify_session(None) is None  # type: ignore[arg-type]


def test_session_rejects_tampered_signature():
    token = security.sign_session(12345)
    # Flip a byte in the signature suffix.
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    assert security.verify_session(bad) is None


def test_session_rejects_expired_token():
    token = security.sign_session(999)
    # Sleep slightly longer than max_age so the token age exceeds the limit.
    # (itsdangerous rounds to whole seconds, so leave a safe margin.)
    time.sleep(2.1)
    assert security.verify_session(token, max_age=1) is None


def test_oauth_state_carries_invite_code():
    state = security.sign_oauth_state(invite_code="ABC123")
    payload = security.verify_oauth_state(state)
    assert payload is not None
    assert payload["invite"] == "ABC123"
    assert payload["admin_bootstrap"] is False
    assert "nonce" in payload and isinstance(payload["nonce"], str)


def test_oauth_state_without_invite():
    state = security.sign_oauth_state()
    payload = security.verify_oauth_state(state)
    assert payload is not None
    assert payload["invite"] is None


def test_oauth_state_rejects_tampered():
    state = security.sign_oauth_state(invite_code="X")
    bad = state[:-2] + "ZZ"
    assert security.verify_oauth_state(bad) is None


def test_session_and_oauth_state_use_different_salts():
    """A session token must not deserialize as an OAuth state, or vice versa."""
    sess = security.sign_session(42)
    assert security.verify_oauth_state(sess) is None

    state = security.sign_oauth_state(invite_code="X")
    assert security.verify_session(state) is None

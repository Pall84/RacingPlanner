"""Tests for the Strava push-subscription webhook endpoints.

Focus is on the route surface: handshake verification, event filtering,
and background-task dispatch. The per-event sync logic in
`_process_event` is exercised via unit-level calls to
`ensure_activity_synced` / `delete_activity` in the sync module; here
we only assert that the webhook routes WOULD queue the right work.

Pattern: override FastAPI Depends + monkeypatch the module-level
helpers (async_session, background task function) so no live DB or
Strava API is required.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_webhooks


# ── Fixtures ─────────────────────────────────────────────────────────────────

class _FakeResult:
    """Mimics the `scalar_one_or_none()` return of an AsyncSession execute."""
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Minimal async session — returns a pre-baked athlete-lookup result."""
    def __init__(self, known_athlete_id: int | None):
        self._known = known_athlete_id

    async def execute(self, *args, **kwargs):
        # The only execute() in the route code path is the Athlete lookup;
        # just return the configured result regardless of query shape.
        return _FakeResult(self._known)


def _fake_session_factory(known_athlete_id: int | None):
    """Build an async context manager that yields a fake session."""
    @asynccontextmanager
    async def _ctx():
        yield _FakeSession(known_athlete_id)
    return _ctx


def _build_app(
    verify_token: str = "test-token",
    known_athlete_id: int | None = 999,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> tuple[FastAPI, list]:
    """Build a tiny FastAPI app that only mounts the webhooks router.

    Returns (app, queued_events). The list is appended to every time the
    route would queue `_process_event` — tests assert on its contents.
    """
    app = FastAPI()
    app.include_router(routes_webhooks.router)

    # Patch settings.strava_webhook_verify_token (cached via lru_cache in
    # get_settings). Simplest: swap out the whole get_settings function.
    from app import config as config_module
    fake_settings = config_module.Settings(
        strava_webhook_verify_token=verify_token,
    )
    monkeypatch.setattr(
        routes_webhooks, "get_settings", lambda: fake_settings,
    )

    # Replace the module-level async_session with a factory that yields
    # our fake. Only the POST route hits it (for the athlete lookup).
    monkeypatch.setattr(
        routes_webhooks,
        "async_session",
        _fake_session_factory(known_athlete_id),
    )

    # Intercept the background task dispatch: record calls instead of
    # running them. FastAPI's BackgroundTasks.add_task runs the task after
    # the response is sent, and doing real DB/Strava work in tests is
    # both unnecessary and painful.
    queued: list = []

    def _record(owner_id, object_id, aspect_type):
        queued.append((owner_id, object_id, aspect_type))

    monkeypatch.setattr(routes_webhooks, "_process_event", _record)
    return app, queued


# ── GET handshake ────────────────────────────────────────────────────────────

def test_handshake_accepts_valid_token(monkeypatch):
    app, _ = _build_app(verify_token="secret", monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "secret",
            "hub.challenge": "abc123",
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"hub.challenge": "abc123"}


def test_handshake_rejects_wrong_token(monkeypatch):
    app, _ = _build_app(verify_token="secret", monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 403
    # Critical: do NOT echo the challenge in any form when the token is wrong,
    # otherwise an attacker could use us as an oracle to set up their own sub.
    assert "abc" not in resp.text


def test_handshake_rejects_empty_configured_token(monkeypatch):
    """If the verify_token is unset (empty string), reject all handshakes
    instead of accepting any request that happens to also pass empty."""
    app, _ = _build_app(verify_token="", monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 403


def test_handshake_rejects_unknown_mode(monkeypatch):
    app, _ = _build_app(verify_token="secret", monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.get(
        "/api/webhooks/strava",
        params={
            "hub.mode": "unsubscribe",
            "hub.verify_token": "secret",
            "hub.challenge": "abc",
        },
    )
    assert resp.status_code == 400


# ── POST events ──────────────────────────────────────────────────────────────

def _event_body(**overrides):
    return {
        "aspect_type": overrides.get("aspect_type", "create"),
        "object_type": overrides.get("object_type", "activity"),
        "object_id": overrides.get("object_id", 12345),
        "owner_id": overrides.get("owner_id", 999),
        "event_time": 1700000000,
        "subscription_id": 1,
        "updates": {},
    }


def test_create_event_queues_work_for_known_athlete(monkeypatch):
    app, queued = _build_app(known_athlete_id=999, monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post("/api/webhooks/strava", json=_event_body())
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert queued == [(999, 12345, "create")]


def test_update_event_same_path_as_create(monkeypatch):
    app, queued = _build_app(known_athlete_id=999, monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/webhooks/strava",
        json=_event_body(aspect_type="update", object_id=67890),
    )
    assert resp.status_code == 200
    assert queued == [(999, 67890, "update")]


def test_delete_event_queues_delete(monkeypatch):
    app, queued = _build_app(known_athlete_id=999, monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/webhooks/strava",
        json=_event_body(aspect_type="delete"),
    )
    assert resp.status_code == 200
    assert queued == [(999, 12345, "delete")]


def test_unknown_owner_returns_200_but_queues_nothing(monkeypatch):
    # Athlete lookup returns None → no task queued, still 200.
    app, queued = _build_app(known_athlete_id=None, monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post("/api/webhooks/strava", json=_event_body(owner_id=999))
    assert resp.status_code == 200
    assert resp.json()["reason"] == "unknown athlete"
    assert queued == []


def test_athlete_object_type_ignored(monkeypatch):
    app, queued = _build_app(monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/webhooks/strava",
        json=_event_body(object_type="athlete"),
    )
    assert resp.status_code == 200
    assert resp.json()["reason"] == "non-activity event"
    assert queued == []


def test_unknown_aspect_type_ignored(monkeypatch):
    app, queued = _build_app(monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/webhooks/strava",
        json=_event_body(aspect_type="bogus"),
    )
    assert resp.status_code == 200
    assert resp.json()["reason"] == "unknown aspect_type"
    assert queued == []


def test_malformed_body_returns_200_and_is_ignored(monkeypatch):
    """Strava retries any non-2xx. A malformed body should never trigger
    a retry storm — swallow and log, return 200."""
    app, queued = _build_app(monkeypatch=monkeypatch)
    client = TestClient(app)

    # Missing required fields: aspect_type, object_type, object_id, owner_id.
    resp = client.post("/api/webhooks/strava", json={"garbage": "yes"})
    assert resp.status_code == 200
    assert resp.json()["reason"] == "malformed body"
    assert queued == []


def test_non_json_body_returns_200(monkeypatch):
    app, queued = _build_app(monkeypatch=monkeypatch)
    client = TestClient(app)

    resp = client.post(
        "/api/webhooks/strava",
        content=b"not json at all",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert queued == []

"""Regression test: ensure unhandled exceptions return a JSON 500 with CORS.

Without the global exception handler in `app.main`, Starlette's outer
ServerErrorMiddleware handles the 500 and the browser gets a response
with no CORS headers — which the browser reports as the opaque
"Failed to fetch". This test catches that regression.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import _unhandled_exception_handler


def _build_test_app() -> FastAPI:
    """Minimal FastAPI app mirroring the real one's CORS + handler wiring."""
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.exception_handler(Exception)(_unhandled_exception_handler)

    @app.get("/boom")
    async def boom() -> dict:
        raise RuntimeError("deliberate explosion")

    return app


def test_unhandled_exception_returns_json_500_with_cors():
    # raise_server_exceptions=False: let the 500 response come back instead of
    # re-raising into the test. Mirrors real-world (browser) behavior.
    client = TestClient(_build_test_app(), raise_server_exceptions=False)
    resp = client.get("/boom", headers={"Origin": "http://localhost:5173"})

    assert resp.status_code == 500
    body = resp.json()
    assert "RuntimeError" in body["detail"]
    assert "deliberate explosion" in body["detail"]
    # CRITICAL: CORS headers must be present so the browser actually sees the body.
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert resp.headers.get("access-control-allow-credentials") == "true"

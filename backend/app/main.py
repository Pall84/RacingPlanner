import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    routes_activities,
    routes_admin,
    routes_auth,
    routes_fitness,
    routes_garmin,
    routes_goals,
    routes_races,
    routes_sync,
    routes_webhooks,
)
from app.config import get_settings
from app.database import engine

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("racingplanner")

if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)

app = FastAPI(title="RacingPlanner API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_admin.router)
app.include_router(routes_activities.router)
app.include_router(routes_fitness.router)
app.include_router(routes_sync.router)
app.include_router(routes_races.router)
app.include_router(routes_goals.router)
app.include_router(routes_garmin.router)
app.include_router(routes_webhooks.router)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for exceptions that escape route handlers.

    Without this, Starlette's outer ServerErrorMiddleware returns a bare 500
    — and Starlette's CORSMiddleware does NOT decorate exception-handler
    responses (this is a longstanding quirk). The browser then reports the
    useless "Failed to fetch". So we log the full traceback AND add CORS
    headers by hand to any response whose Origin is in our allowlist.

    We intentionally DO NOT catch HTTPException — FastAPI's built-in handler
    already formats those correctly (401/403/404 etc. pass through unchanged).
    """
    log.exception(
        "Unhandled %s on %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
    )

    # Manually add CORS headers — CORSMiddleware skips exception responses.
    origin = request.headers.get("origin")
    cors_headers: dict[str, str] = {}
    if origin and origin in settings.allowed_origins:
        cors_headers = {
            "access-control-allow-origin": origin,
            "access-control-allow-credentials": "true",
            "vary": "Origin",
        }

    # Never echo str(exc) to the client — exception messages can contain DB
    # URLs with embedded credentials (asyncpg), Strava refresh tokens (in
    # httpx response bodies), stack frame fragments, etc. The full traceback
    # is in Render logs via `log.exception`; clients get a generic message.
    # In non-production we include the exception class to ease local debug.
    detail = "Internal server error"
    if not settings.is_production:
        detail = f"{type(exc).__name__}: internal server error (see logs)"

    return JSONResponse(
        status_code=500,
        content={"detail": detail},
        headers=cors_headers,
    )


@app.get("/health")
async def health() -> JSONResponse:
    """Health check for Render's auto-restart.

    Returns HTTP 503 when the DB is unreachable so Render actually restarts
    the container instead of leaving a broken instance serving traffic.
    Previously returned 200 with `database: error`, which Render's liveness
    probe happily accepted — the container stayed broken until manual redeploy.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return JSONResponse(
            content={"status": "ok", "database": "connected"}, status_code=200,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("DB health check failed: %s", e)
        return JSONResponse(
            content={"status": "degraded", "database": "error"}, status_code=503,
        )

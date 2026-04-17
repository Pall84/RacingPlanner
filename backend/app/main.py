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

    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {str(exc)[:500]}"},
        headers=cors_headers,
    )


@app.get("/health")
async def health() -> dict:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:  # noqa: BLE001
        log.warning("DB health check failed: %s", e)
        db_status = "error"
    return {"status": "ok", "database": db_status}

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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

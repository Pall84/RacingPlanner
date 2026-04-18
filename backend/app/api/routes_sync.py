import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.database import SessionLocal as async_session
from app.models.schema import Activity
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/sync", tags=["sync"])

# Global queue per athlete_id for SSE
_progress_queues: dict[int, asyncio.Queue] = {}


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


@router.get("/status")
async def sync_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)

    total_result = await db.execute(
        select(func.count()).where(Activity.athlete_id == athlete_id)
    )
    total = total_result.scalar() or 0

    pending_streams = await db.execute(
        select(func.count()).where(
            Activity.athlete_id == athlete_id,
            Activity.streams_synced == 0,
        )
    )
    pending = pending_streams.scalar() or 0

    pending_metrics = await db.execute(
        select(func.count()).where(
            Activity.athlete_id == athlete_id,
            Activity.metrics_computed == 0,
        )
    )
    metrics_pending = pending_metrics.scalar() or 0

    return {
        "activities_count": total,
        "streams_pending": pending,
        "metrics_pending": metrics_pending,
    }


@router.post("/full")
async def full_sync(
    request: Request,
    background_tasks: BackgroundTasks,
):
    athlete_id = _get_athlete_id(request)
    import logging

    from app.analytics.compute_pipeline import run_full_pipeline

    log = logging.getLogger("racingplanner.sync.full")
    q: asyncio.Queue = asyncio.Queue()
    _progress_queues[athlete_id] = q

    async def _run():
        # Errors here can't reach the user synchronously — the HTTP response
        # has already returned. Report them via the SSE progress queue so the
        # frontend can show them, and close the stream cleanly with DONE.
        from app.strava.auth import StravaAuthRevoked
        try:
            async with async_session() as session:
                await run_full_pipeline(session, athlete_id, q, full_sync=True)
        except StravaAuthRevoked:
            log.warning("Strava auth revoked for athlete %s — sync halted", athlete_id)
            await q.put("ERROR: Strava access was revoked. Please log out and log back in.")
            await q.put("DONE")
        except RuntimeError as e:
            # Rate-limiter errors mid-pipeline (e.g. during stream fetching)
            # are expected — don't log as "crashed" and show a friendlier
            # message than the generic exception branch.
            if "rate limit" in str(e).lower():
                log.warning("Rate limit hit during sync for athlete %s: %s", athlete_id, e)
                await q.put(
                    "WARNING: Strava rate limit reached. Partial sync complete — "
                    "re-run sync in 15 min to finish."
                )
                await q.put("DONE")
            else:
                log.exception("Full sync pipeline crashed for athlete %s", athlete_id)
                await q.put(f"ERROR: Pipeline failed: {type(e).__name__}: {e}")
                await q.put("DONE")
        except Exception as e:  # noqa: BLE001
            log.exception("Full sync pipeline crashed for athlete %s", athlete_id)
            await q.put(f"ERROR: Pipeline failed: {type(e).__name__}: {e}")
            await q.put("DONE")

    background_tasks.add_task(_run)
    return {"queued": True, "message": "Full sync started"}


@router.get("/progress")
async def progress_stream(request: Request):
    athlete_id = _get_athlete_id(request)

    q = _progress_queues.get(athlete_id)
    if not q:
        q = asyncio.Queue()
        _progress_queues[athlete_id] = q

    async def event_generator():
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {msg}\n\n"
                    if msg == "DONE":
                        break
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            # Drop the queue from the registry so it doesn't leak across
            # sync runs. If a new run has already replaced it (unlikely, but
            # possible on reconnect), leave the newer queue alone.
            if _progress_queues.get(athlete_id) is q:
                _progress_queues.pop(athlete_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

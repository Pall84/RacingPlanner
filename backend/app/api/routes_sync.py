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
    from app.analytics.compute_pipeline import run_full_pipeline

    q: asyncio.Queue = asyncio.Queue()
    _progress_queues[athlete_id] = q

    async def _run():
        async with async_session() as session:
            await run_full_pipeline(session, athlete_id, q, full_sync=True)

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
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {msg}\n\n"
                if msg == "DONE":
                    break
            except TimeoutError:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

"""FastAPI dependencies — DB session and the current-athlete resolver.

Replaces the per-route `request.cookies.get("athlete_id")` pattern that was
repeated across every handler in the original app. Any route that needs the
authenticated user simply depends on `get_current_athlete`. Admin-only routes
depend on `get_admin_athlete`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models.schema import Athlete
from app.security import SESSION_COOKIE_NAME, verify_session


async def get_db() -> AsyncIterator[AsyncSession]:
    """Per-request DB session.

    Unit-of-work pattern: each request is one transaction. On clean success
    we commit all pending changes; on any exception we roll back. This
    means handlers generally don't need explicit commits — `db.flush()`
    inside a handler only makes changes visible in-session, but we promote
    them to durable on the way out.

    Handlers that have already committed explicitly (for intra-request
    isolation, or to split long work into multiple transactions) are fine:
    a second commit on an already-committed session is a cheap no-op.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            await session.commit()


async def get_current_athlete(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Athlete:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
    athlete = result.scalar_one_or_none()
    if athlete is None:
        # Valid signature but the underlying athlete row is gone — treat as logged out.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists",
        )
    return athlete


async def get_admin_athlete(
    athlete: Athlete = Depends(get_current_athlete),
) -> Athlete:
    if not athlete.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return athlete

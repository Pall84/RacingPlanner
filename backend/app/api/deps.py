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
    async with SessionLocal() as session:
        yield session


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

"""
Athlete goals — CRUD with inline progress computation.
"""
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.schema import Activity, AthleteGoal, Race
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/goals", tags=["goals"])


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


async def _compute_progress(db: AsyncSession, goal: AthleteGoal, athlete_id: int) -> dict:
    """Compute current progress toward a goal."""
    today = date.today()
    current = 0.0

    if goal.goal_type == "weekly_distance":
        monday = today - timedelta(days=today.weekday())
        result = await db.execute(
            select(func.sum(Activity.distance))
            .where(
                Activity.athlete_id == athlete_id,
                Activity.start_date >= monday.isoformat(),
            )
        )
        current = (result.scalar() or 0) / 1000  # metres → km

    elif goal.goal_type == "annual_distance":
        ytd_start = f"{today.year}-01-01"
        result = await db.execute(
            select(func.sum(Activity.distance))
            .where(
                Activity.athlete_id == athlete_id,
                Activity.start_date >= ytd_start,
            )
        )
        current = (result.scalar() or 0) / 1000

    elif goal.goal_type == "weekly_runs":
        monday = today - timedelta(days=today.weekday())
        result = await db.execute(
            select(func.count())
            .where(
                Activity.athlete_id == athlete_id,
                Activity.start_date >= monday.isoformat(),
            )
        )
        current = result.scalar() or 0

    elif goal.goal_type == "race_time" and goal.race_id:
        result = await db.execute(
            select(Race.predicted_time_sec).where(Race.id == goal.race_id)
        )
        pred = result.scalar()
        if pred:
            current = pred  # compare predicted vs target

    pct = round(current / goal.target_value * 100, 1) if goal.target_value > 0 else 0
    return {
        "current": round(current, 1),
        "target": goal.target_value,
        "pct": min(pct, 100),
    }


def _goal_row(goal: AthleteGoal, progress: dict) -> dict:
    return {
        "id": goal.id,
        "goal_type": goal.goal_type,
        "target_value": goal.target_value,
        "target_unit": goal.target_unit,
        "target_date": goal.target_date,
        "race_id": goal.race_id,
        "created_at": goal.created_at,
        "progress": progress,
    }


_GOAL_LABELS = {
    "weekly_distance": "Weekly Distance",
    "annual_distance": "Annual Distance",
    "weekly_runs": "Weekly Runs",
    "race_time": "Race Goal Time",
}


@router.get("")
async def list_goals(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(AthleteGoal)
        .where(AthleteGoal.athlete_id == athlete_id)
        .order_by(AthleteGoal.created_at)
    )
    goals = result.scalars().all()

    rows = []
    for g in goals:
        progress = await _compute_progress(db, g, athlete_id)
        row = _goal_row(g, progress)
        row["label"] = _GOAL_LABELS.get(g.goal_type, g.goal_type)
        rows.append(row)

    return rows


class GoalCreateRequest(BaseModel):
    goal_type: str      # weekly_distance, annual_distance, weekly_runs, race_time
    target_value: float
    target_unit: str | None = None
    target_date: str | None = None
    race_id: int | None = None


@router.post("")
async def create_goal(
    body: GoalCreateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)

    # Auto-set unit
    unit = body.target_unit
    if not unit:
        unit_map = {
            "weekly_distance": "km",
            "annual_distance": "km",
            "weekly_runs": "runs",
            "race_time": "seconds",
        }
        unit = unit_map.get(body.goal_type, "")

    goal = AthleteGoal(
        athlete_id=athlete_id,
        goal_type=body.goal_type,
        target_value=body.target_value,
        target_unit=unit,
        target_date=body.target_date,
        race_id=body.race_id,
        created_at=int(time.time()),
    )
    db.add(goal)
    await db.flush()

    progress = await _compute_progress(db, goal, athlete_id)
    row = _goal_row(goal, progress)
    row["label"] = _GOAL_LABELS.get(goal.goal_type, goal.goal_type)
    return row


@router.delete("/{goal_id}", status_code=204)
async def delete_goal(
    goal_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(AthleteGoal).where(
            AthleteGoal.id == goal_id,
            AthleteGoal.athlete_id == athlete_id,
        )
    )
    goal = result.scalar_one_or_none()
    if not goal:
        raise HTTPException(status_code=404)
    await db.delete(goal)

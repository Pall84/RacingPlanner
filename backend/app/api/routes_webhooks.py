"""Public webhook endpoints (no auth, signature-less).

Currently: Strava push subscriptions. Strava sends events within seconds
of an athlete creating / updating / deleting an activity on their side,
so RacingPlanner can refresh automatically instead of making the user
press "Sync New".

Design notes
------------
- **Verify handshake (GET)**: Strava calls this once during subscription
  creation with `hub.mode=subscribe` + our shared `verify_token`. We echo
  back `hub.challenge` only if the token matches.

- **Event POST**: Strava does NOT sign event bodies — there is no HMAC to
  verify. Security rests on the callback URL being unguessable (the
  webhook endpoint path) plus the fact that bogus events only trigger
  us to call Strava's API (which fails without a valid token for that
  athlete). We still validate the payload shape and reject unknown
  athletes before queueing any work.

- **Response timing**: Strava requires a 2xx response within ~2 seconds
  or it retries. The actual sync (Strava API calls, metrics recompute)
  runs as a FastAPI BackgroundTask; we return 200 immediately.

- **Events we care about**: `object_type == "activity"` with
  `aspect_type in {create, update, delete}`. Athlete events (deauth
  notifications) could be wired up later to auto-disconnect a user.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import SessionLocal as async_session
from app.models.schema import Activity, Athlete

log = logging.getLogger("racingplanner.webhooks")

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


class StravaWebhookEvent(BaseModel):
    """Shape of a Strava push-subscription event payload.

    See https://developers.strava.com/docs/webhooks/ — all fields the API
    reference lists as "Always present" on the event object. Extras are
    ignored (Pydantic's default behaviour). Keeping typing loose on
    `updates` because its schema varies by aspect_type and we don't act
    on it today.
    """
    aspect_type: str         # "create" | "update" | "delete"
    object_type: str         # "activity" | "athlete"
    object_id: int           # activity_id or athlete_id depending on object_type
    owner_id: int            # athlete_id (maps to our Athlete.id since we use Strava IDs)
    event_time: int | None = None
    subscription_id: int | None = None
    updates: dict = {}


# ── GET: subscription verification handshake ─────────────────────────────────

@router.get("/strava")
async def strava_verify_subscription(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """Handshake Strava triggers when a subscription is created.

    Strava hits us with a GET containing hub.mode=subscribe, the shared
    verify_token, and a random hub.challenge we need to echo back. This
    is Strava's proof that the callback URL is actually controlled by
    someone who knows the token.
    """
    settings = get_settings()
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="unsupported hub.mode")

    expected = settings.strava_webhook_verify_token
    if not expected or hub_verify_token != expected:
        log.warning(
            "strava webhook verify token mismatch (configured=%s, got=%s)",
            bool(expected), bool(hub_verify_token),
        )
        raise HTTPException(status_code=403, detail="verify token mismatch")

    return {"hub.challenge": hub_challenge}


# ── POST: incoming event ─────────────────────────────────────────────────────

@router.post("/strava")
async def strava_event(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Receive a push-subscription event from Strava.

    Always returns 200. Strava treats any non-2xx as a delivery failure and
    retries — we'd rather silently drop malformed events than cause it to
    hammer us. Diagnostic info goes to the logs instead of the response.

    The actual sync is queued as a BackgroundTask and runs after this
    response has been sent (Strava's 2-second timeout is tight).
    """
    # Parse manually so a bad body still returns 200 + log, not a 422.
    try:
        body = await request.json()
        event = StravaWebhookEvent(**body)
    except Exception as e:  # noqa: BLE001
        log.warning("malformed strava webhook body: %s: %s", type(e).__name__, e)
        return {"status": "ignored", "reason": "malformed body"}

    # Early filters — return without queueing any work.
    if event.object_type != "activity":
        # Athlete events (e.g. deauth notifications) are ignored for now.
        log.info("strava webhook ignoring object_type=%s", event.object_type)
        return {"status": "ignored", "reason": "non-activity event"}

    if event.aspect_type not in ("create", "update", "delete"):
        log.info("strava webhook ignoring aspect_type=%s", event.aspect_type)
        return {"status": "ignored", "reason": "unknown aspect_type"}

    # Verify owner is a known athlete BEFORE queueing work. Done in an
    # independent session since this handler doesn't use Depends(get_db)
    # — we want the endpoint to stay fast.
    async with async_session() as session:
        result = await session.execute(
            select(Athlete.id).where(Athlete.id == event.owner_id)
        )
        if result.scalar_one_or_none() is None:
            # Unknown owner — could be an ex-user or an event that escaped
            # from a stale subscription. Return 200 (never leak athlete
            # existence to the caller) but don't queue work.
            log.info("strava webhook ignoring unknown owner=%s", event.owner_id)
            return {"status": "ignored", "reason": "unknown athlete"}

    log.info(
        "strava webhook received aspect=%s object=%s owner=%s",
        event.aspect_type, event.object_id, event.owner_id,
    )
    background_tasks.add_task(
        _process_event,
        event.owner_id,
        event.object_id,
        event.aspect_type,
    )
    return {"status": "queued"}


# ── Background task ──────────────────────────────────────────────────────────

async def _process_event(
    athlete_id: int,
    activity_id: int,
    aspect_type: str,
) -> None:
    """Async handler for a filtered webhook event.

    Runs AFTER the 200 has been sent. Opens its own async session because
    the request scope is already gone. Errors are logged and swallowed —
    there's no client waiting for a response. StravaAuthRevoked is treated
    as a warning, not an error: it's the user's job to reauthorize, and
    subsequent webhook events for them will keep failing the same way
    until they do.
    """
    from app.analytics.classification_engine import classify_workout
    from app.analytics.compute_pipeline import compute_metrics_for_activity
    from app.config import get_athlete_settings
    from app.models.schema import ActivityMetrics
    from app.strava.auth import StravaAuthRevoked
    from app.strava.sync import delete_activity, ensure_activity_synced
    from sqlalchemy import text as sa_text

    try:
        async with async_session() as db:
            if aspect_type == "delete":
                await delete_activity(db, athlete_id, activity_id)
                await db.commit()
                return

            # create or update → fetch + refresh + recompute metrics.
            synced = await ensure_activity_synced(db, athlete_id, activity_id)
            if not synced:
                # Non-run activity or refresh returned False. Nothing more to do.
                await db.commit()
                return

            # Recompute metrics + classification (same shape as /refresh route).
            athlete = (
                await db.execute(select(Athlete).where(Athlete.id == athlete_id))
            ).scalar_one_or_none()
            activity = (
                await db.execute(
                    select(Activity).where(
                        Activity.id == activity_id,
                        Activity.athlete_id == athlete_id,
                    )
                )
            ).scalar_one_or_none()

            if activity and activity.streams_synced and athlete:
                await compute_metrics_for_activity(
                    db, activity, get_athlete_settings(athlete),
                )
                # Classification needs recent-30 averages like the /refresh route.
                try:
                    recent = await db.execute(
                        sa_text("""
                            SELECT AVG(moving_time), AVG(distance)
                            FROM (
                                SELECT moving_time, distance FROM activities
                                WHERE athlete_id = :aid
                                  AND moving_time > 0 AND distance > 0
                                ORDER BY start_date DESC LIMIT 30
                            ) AS recent30
                        """),
                        {"aid": athlete_id},
                    )
                    avg_time, avg_dist = recent.one()
                    if avg_time and avg_dist:
                        metrics_row = (
                            await db.execute(
                                select(ActivityMetrics).where(
                                    ActivityMetrics.activity_id == activity_id
                                )
                            )
                        ).scalar_one_or_none()
                        if metrics_row:
                            metrics_row.workout_type = classify_workout(
                                metrics={
                                    "z1_seconds": metrics_row.z1_seconds,
                                    "z2_seconds": metrics_row.z2_seconds,
                                    "z3_seconds": metrics_row.z3_seconds,
                                    "z4_seconds": metrics_row.z4_seconds,
                                    "z5_seconds": metrics_row.z5_seconds,
                                    "pacing_cv_pct": metrics_row.pacing_cv_pct,
                                },
                                activity={
                                    "name": activity.name or "",
                                    "moving_time": activity.moving_time,
                                    "distance": activity.distance,
                                },
                                recent_avg_duration=float(avg_time),
                                recent_avg_distance=float(avg_dist),
                            )
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "webhook classification failed activity=%s: %s",
                        activity_id, e,
                    )

            await db.commit()

    except StravaAuthRevoked:
        log.warning(
            "strava webhook halted — auth revoked for athlete=%s, event for activity=%s dropped",
            athlete_id, activity_id,
        )
    except Exception:  # noqa: BLE001
        log.exception(
            "strava webhook task failed athlete=%s activity=%s aspect=%s",
            athlete_id, activity_id, aspect_type,
        )

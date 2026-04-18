import asyncio
import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.models.schema import Activity, ActivityStream, Lap
from app.strava.auth import StravaAuthRevoked, get_valid_token
from app.strava.client import StravaClient

log = logging.getLogger("racingplanner.strava.sync")

RUN_TYPES = {"Run", "TrailRun", "VirtualRun", "Hike", "Walk"}


def _parse_iso(dt_str: str) -> int:
    """Convert ISO 8601 UTC string to Unix timestamp."""
    dt_str = dt_str.replace("Z", "+00:00")
    return int(datetime.fromisoformat(dt_str).timestamp())


async def sync_activities(
    db,
    athlete_id: int,
    full_sync: bool = False,
    progress_queue=None,
) -> int:
    token = await get_valid_token(db, athlete_id)
    client = StravaClient(token, athlete_id)

    # Determine starting point
    after_ts = None
    if not full_sync:
        result = await db.execute(
            select(func.max(Activity.start_date)).where(Activity.athlete_id == athlete_id)
        )
        max_date = result.scalar_one_or_none()
        if max_date:
            after_ts = _parse_iso(max_date) + 1

    new_count = 0
    page = 1
    hit_rate_limit = False
    while True:
        if progress_queue:
            await progress_queue.put(f"Fetching activities page {page}...")

        try:
            activities = await client.get_activities(
                page=page, per_page=50, after=after_ts
            )
        except RuntimeError as e:
            # Rate limiter raises RuntimeError with "rate limit" in the message.
            # Treat this as end-of-sync rather than pipeline-crashing: the
            # activities we already persisted are valid, and steps 5-9 should
            # still run on that data. The next sync picks up where this one
            # stopped via the `after_ts` filter.
            if "rate limit" in str(e).lower():
                hit_rate_limit = True
                log.warning(
                    "sync_activities hit rate limit at page %s for athlete %s — "
                    "returning partial results (%s new so far)",
                    page, athlete_id, new_count,
                )
                if progress_queue:
                    await progress_queue.put(
                        f"Strava rate limit reached after {new_count} new activities "
                        f"(page {page}). Continuing with partial data; rerun sync in 15 min to fetch the rest."
                    )
                break
            raise

        if not activities:
            break

        for raw in activities:
            activity_type = raw.get("sport_type") or raw.get("type", "")
            if activity_type not in RUN_TYPES:
                continue
            await _insert_activity_row(db, athlete_id, raw)
            new_count += 1

        page += 1
        await asyncio.sleep(0.1)  # Small courtesy delay between pages

    await db.flush()
    if progress_queue and not hit_rate_limit:
        await progress_queue.put(f"Synced {new_count} new activities.")
    return new_count


async def sync_streams(db, activity_id: int, athlete_id: int, progress_queue=None) -> bool:
    token = await get_valid_token(db, athlete_id)
    client = StravaClient(token, athlete_id)

    try:
        streams = await client.get_streams(activity_id)
    except Exception as e:
        if progress_queue:
            await progress_queue.put(f"Stream error for {activity_id}: {e}")
        return False

    for stream_type, stream_data in streams.items():
        if not isinstance(stream_data, dict):
            continue
        data = stream_data.get("data", [])
        stmt = insert(ActivityStream).values(
            activity_id=activity_id,
            stream_type=stream_type,
            data_json=json.dumps(data),
            resolution=stream_data.get("resolution"),
            series_type=stream_data.get("series_type"),
            created_at=int(time.time()),
        ).on_conflict_do_update(
            index_elements=["activity_id", "stream_type"],
            set_={"data_json": json.dumps(data)},
        )
        await db.execute(stmt)

    # Mark streams synced
    result = await db.execute(select(Activity).where(Activity.id == activity_id))
    activity = result.scalar_one_or_none()
    if activity:
        activity.streams_synced = 1

    # Sync laps
    try:
        laps_data = await client.get_laps(activity_id)
        for raw_lap in laps_data:
            stmt = insert(Lap).values(
                activity_id=activity_id,
                strava_lap_id=raw_lap.get("id"),
                lap_index=raw_lap.get("lap_index"),
                name=raw_lap.get("name"),
                distance=raw_lap.get("distance"),
                moving_time=raw_lap.get("moving_time"),
                elapsed_time=raw_lap.get("elapsed_time"),
                average_speed=raw_lap.get("average_speed"),
                max_speed=raw_lap.get("max_speed"),
                average_heartrate=raw_lap.get("average_heartrate"),
                max_heartrate=raw_lap.get("max_heartrate"),
                average_cadence=raw_lap.get("average_cadence"),
                total_elevation_gain=raw_lap.get("total_elevation_gain"),
                pace_sec_per_km=(1000.0 / raw_lap["average_speed"] if raw_lap.get("average_speed") else None),
                split_type=raw_lap.get("split"),
            ).on_conflict_do_nothing(index_elements=["id"])
            await db.execute(stmt)
        if activity:
            activity.laps_synced = 1
    except StravaAuthRevoked:
        # Propagate — a revoked token is not "laps are optional", it means
        # the whole sync should halt and the user needs to reauthorize.
        raise
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as e:
        # Laps are optional; a missing/malformed response for them shouldn't
        # fail the activity sync. Log so we can see the pattern if it recurs.
        log.warning(
            "laps sync failed for activity=%s: %s: %s",
            activity_id, type(e).__name__, e,
        )

    await db.flush()
    return True


async def refresh_activity(db, activity_id: int, athlete_id: int) -> bool:
    """
    Re-fetch a single activity's metadata, streams, and laps from Strava.

    Safety rules:
    - If activity.treadmill_corrected is set, distance/elevation totals on the
      activity row are NOT overwritten (they reflect user corrections).
    - Existing lap rows are updated in-place; corrected_distance and
      corrected_elevation_gain are never touched.
    - New laps that weren't in the DB are inserted fresh.
    - All streams are always overwritten with fresh data.
    - metrics_computed is reset to 0 so the next pipeline run recomputes.
    """
    token = await get_valid_token(db, athlete_id)
    client = StravaClient(token, athlete_id)

    # Fetch detailed activity from Strava. We let specific errors bubble up
    # so the route handler can translate them to useful HTTP responses;
    # only return False for "activity doesn't exist in our DB" below.
    raw = await client.get_activity(activity_id)

    act_result = await db.execute(
        select(Activity).where(Activity.id == activity_id, Activity.athlete_id == athlete_id)
    )
    activity = act_result.scalar_one_or_none()
    if not activity:
        return False

    # Always update non-distance/elevation metadata
    activity.name = raw.get("name") or activity.name
    activity.type = raw.get("type") or activity.type
    activity.sport_type = raw.get("sport_type") or activity.sport_type
    activity.moving_time = raw.get("moving_time") or activity.moving_time
    activity.elapsed_time = raw.get("elapsed_time") or activity.elapsed_time
    activity.average_heartrate = raw.get("average_heartrate") or activity.average_heartrate
    activity.max_heartrate = raw.get("max_heartrate") or activity.max_heartrate
    activity.average_cadence = raw.get("average_cadence") or activity.average_cadence
    activity.average_watts = raw.get("average_watts") or activity.average_watts
    activity.max_watts = raw.get("max_watts") or activity.max_watts
    activity.map_summary_polyline = (
        raw.get("map", {}).get("summary_polyline") or activity.map_summary_polyline
    )
    activity.raw_json = json.dumps(raw)

    # Only overwrite distance/elevation if user hasn't applied treadmill corrections
    if not activity.treadmill_corrected:
        activity.distance = raw.get("distance") or activity.distance
        activity.total_elevation_gain = raw.get("total_elevation_gain") or activity.total_elevation_gain
        activity.elev_low = raw.get("elev_low") or activity.elev_low
        activity.elev_high = raw.get("elev_high") or activity.elev_high
        activity.average_speed = raw.get("average_speed") or activity.average_speed
        activity.max_speed = raw.get("max_speed") or activity.max_speed

    # Re-sync streams (always overwrite with fresh data)
    try:
        streams = await client.get_streams(activity_id)
        for stream_type, stream_data in streams.items():
            if not isinstance(stream_data, dict):
                continue
            data = stream_data.get("data", [])
            stmt = insert(ActivityStream).values(
                activity_id=activity_id,
                stream_type=stream_type,
                data_json=json.dumps(data),
                resolution=stream_data.get("resolution"),
                series_type=stream_data.get("series_type"),
                created_at=int(time.time()),
            ).on_conflict_do_update(
                index_elements=["activity_id", "stream_type"],
                set_={"data_json": json.dumps(data)},
            )
            await db.execute(stmt)
        activity.streams_synced = 1
    except StravaAuthRevoked:
        raise  # See laps-catch comment: reauth should halt the refresh.
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as e:
        log.warning(
            "stream refresh failed for activity=%s: %s: %s",
            activity_id, type(e).__name__, e,
        )

    # Re-sync laps, preserving user corrections
    try:
        laps_data = await client.get_laps(activity_id)

        existing_result = await db.execute(
            select(Lap).where(Lap.activity_id == activity_id)
        )
        existing_by_index = {lap.lap_index: lap for lap in existing_result.scalars().all()}

        for raw_lap in laps_data:
            lap_index = raw_lap.get("lap_index")
            strava_dist = raw_lap.get("distance")
            strava_elev = raw_lap.get("total_elevation_gain")
            strava_speed = raw_lap.get("average_speed")
            existing = existing_by_index.get(lap_index)

            if existing:
                # Update Strava values in-place; never touch correction columns
                existing.name = raw_lap.get("name") or existing.name
                existing.distance = strava_dist if strava_dist is not None else existing.distance
                existing.moving_time = raw_lap.get("moving_time") or existing.moving_time
                existing.elapsed_time = raw_lap.get("elapsed_time") or existing.elapsed_time
                existing.average_speed = strava_speed if strava_speed is not None else existing.average_speed
                existing.max_speed = raw_lap.get("max_speed") or existing.max_speed
                existing.average_heartrate = raw_lap.get("average_heartrate") or existing.average_heartrate
                existing.max_heartrate = raw_lap.get("max_heartrate") or existing.max_heartrate
                existing.average_cadence = raw_lap.get("average_cadence") or existing.average_cadence
                existing.total_elevation_gain = strava_elev if strava_elev is not None else existing.total_elevation_gain
                existing.split_type = raw_lap.get("split") or existing.split_type
                # Only recalculate stored pace if no distance correction is applied
                if existing.corrected_distance is None and strava_speed:
                    existing.pace_sec_per_km = 1000.0 / strava_speed
            else:
                # Lap not previously in DB — insert fresh
                new_lap = Lap(
                    activity_id=activity_id,
                    strava_lap_id=raw_lap.get("id"),
                    lap_index=lap_index,
                    name=raw_lap.get("name"),
                    distance=strava_dist,
                    moving_time=raw_lap.get("moving_time"),
                    elapsed_time=raw_lap.get("elapsed_time"),
                    average_speed=strava_speed,
                    max_speed=raw_lap.get("max_speed"),
                    average_heartrate=raw_lap.get("average_heartrate"),
                    max_heartrate=raw_lap.get("max_heartrate"),
                    average_cadence=raw_lap.get("average_cadence"),
                    total_elevation_gain=strava_elev,
                    pace_sec_per_km=(1000.0 / strava_speed if strava_speed else None),
                    split_type=raw_lap.get("split"),
                )
                db.add(new_lap)

        activity.laps_synced = 1
    except StravaAuthRevoked:
        raise
    except (httpx.HTTPStatusError, httpx.RequestError, KeyError, ValueError) as e:
        log.warning(
            "lap refresh failed for activity=%s: %s: %s",
            activity_id, type(e).__name__, e,
        )

    activity.metrics_computed = 0
    await db.flush()
    return True


async def sync_all_pending_streams(db, athlete_id: int, progress_queue=None):
    result = await db.execute(
        select(Activity.id).where(
            Activity.athlete_id == athlete_id,
            Activity.streams_synced == 0,
            Activity.sport_type.in_(list(RUN_TYPES)),
        ).order_by(Activity.start_date.desc())
    )
    pending_ids = [row[0] for row in result.all()]

    if progress_queue:
        await progress_queue.put(f"Downloading streams for {len(pending_ids)} activities...")

    # Each concurrent task MUST open its own session. SQLAlchemy AsyncSession
    # is not safe for concurrent use — sharing one across asyncio.gather tasks
    # corrupts its internal state and orphans the underlying Postgres
    # connection (which then logs "connection not checked in" when GC'd).
    from app.database import SessionLocal

    semaphore = asyncio.Semaphore(3)

    async def _sync_one(activity_id: int):
        async with semaphore:
            async with SessionLocal() as own_db:
                try:
                    await sync_streams(own_db, activity_id, athlete_id, progress_queue)
                    await own_db.commit()
                except Exception as e:
                    await own_db.rollback()
                    if progress_queue:
                        await progress_queue.put(f"Stream error for {activity_id}: {e}")
            await asyncio.sleep(0.2)

    # return_exceptions=True: one bad activity shouldn't abort the whole batch.
    await asyncio.gather(
        *[_sync_one(aid) for aid in pending_ids],
        return_exceptions=True,
    )


# ── Webhook helpers ──────────────────────────────────────────────────────────

async def _insert_activity_row(db, athlete_id: int, raw: dict) -> None:
    """Insert a single activity row from a Strava JSON payload.

    Shared by `sync_activities` (bulk page loop) and `ensure_activity_synced`
    (webhook single-activity path). Keeping the column list in one place
    means adding a new field from Strava only has to happen once.

    Uses `on_conflict_do_nothing` on PK — a duplicate event for an already-
    known activity is a no-op at this layer. `refresh_activity` handles
    the update side separately.
    """
    stmt = insert(Activity).values(
        id=raw["id"],
        athlete_id=athlete_id,
        name=raw.get("name"),
        type=raw.get("type"),
        sport_type=raw.get("sport_type"),
        start_date=raw.get("start_date"),
        start_date_local=raw.get("start_date_local"),
        timezone=raw.get("timezone"),
        distance=raw.get("distance"),
        moving_time=raw.get("moving_time"),
        elapsed_time=raw.get("elapsed_time"),
        total_elevation_gain=raw.get("total_elevation_gain"),
        elev_low=raw.get("elev_low"),
        elev_high=raw.get("elev_high"),
        average_speed=raw.get("average_speed"),
        max_speed=raw.get("max_speed"),
        average_heartrate=raw.get("average_heartrate"),
        max_heartrate=raw.get("max_heartrate"),
        average_cadence=raw.get("average_cadence"),
        average_watts=raw.get("average_watts"),
        max_watts=raw.get("max_watts"),
        weighted_average_watts=raw.get("weighted_average_watts"),
        suffer_score=raw.get("suffer_score"),
        trainer=int(raw.get("trainer", False)),
        commute=int(raw.get("commute", False)),
        manual=int(raw.get("manual", False)),
        has_heartrate=int(raw.get("has_heartrate", False)),
        kudos_count=raw.get("kudos_count", 0),
        map_summary_polyline=raw.get("map", {}).get("summary_polyline"),
        streams_synced=0,
        metrics_computed=0,
        laps_synced=0,
        raw_json=json.dumps(raw),
        created_at=int(time.time()),
    ).on_conflict_do_nothing(index_elements=["id"])
    await db.execute(stmt)


async def ensure_activity_synced(db, athlete_id: int, activity_id: int) -> bool:
    """Insert the activity row if missing, then refresh streams + laps.

    Called from the Strava webhook handler for `aspect_type in (create, update)`.
    Closes the gap where `refresh_activity` alone returns False on missing rows
    (the common case for fresh `create` events). Returns True if the activity
    is in our DB at exit, False if it was skipped (non-run type).

    Non-run types are filtered here for symmetry with `sync_activities`, so
    the webhook doesn't inflate the DB with cycling / yoga / etc. events.
    """
    result = await db.execute(
        select(Activity).where(
            Activity.id == activity_id,
            Activity.athlete_id == athlete_id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is None:
        # New activity: fetch and insert before handing off to refresh_activity.
        token = await get_valid_token(db, athlete_id)
        client = StravaClient(token, athlete_id)
        raw = await client.get_activity(activity_id)
        activity_type = raw.get("sport_type") or raw.get("type", "")
        if activity_type not in RUN_TYPES:
            log.info(
                "webhook ignoring non-run activity=%s type=%s athlete=%s",
                activity_id, activity_type, athlete_id,
            )
            return False
        await _insert_activity_row(db, athlete_id, raw)
        await db.flush()

    # Either way, delegate to the existing single-activity refresh path.
    # This re-fetches the activity summary one more time (small duplication
    # — worth it to keep one refresh code path) + streams + laps.
    return await refresh_activity(db, activity_id, athlete_id)


async def delete_activity(db, athlete_id: int, activity_id: int) -> bool:
    """Hard-delete an activity row. Called from the Strava webhook handler
    for `aspect_type=delete`.

    ORM cascade on ActivityStream / ActivityMetrics / KmSplit / Lap
    relationships (all configured with `cascade="all, delete-orphan"` in
    schema.py) carries dependents with it. Races linked to this activity
    via `Race.linked_activity_id` are on a nullable FK — the reference
    goes stale, which is already the "not linked" state the UI handles.

    Returns True if a row was deleted, False if it was already gone.
    """
    from sqlalchemy import delete as sa_delete

    result = await db.execute(
        sa_delete(Activity).where(
            Activity.id == activity_id,
            Activity.athlete_id == athlete_id,
        )
    )
    deleted = (result.rowcount or 0) > 0
    if deleted:
        log.info("webhook deleted activity=%s athlete=%s", activity_id, athlete_id)
    return deleted

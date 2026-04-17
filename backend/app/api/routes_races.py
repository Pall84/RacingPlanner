import json
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.schema import Race
from app.security import SESSION_COOKIE_NAME, verify_session

router = APIRouter(prefix="/api/races", tags=["races"])


def _get_athlete_id(request: Request) -> int:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    athlete_id = verify_session(token) if token else None
    if athlete_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return athlete_id


def _fmt_time(sec: float | None) -> str | None:
    if not sec or sec <= 0:
        return None
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _fmt_pace(sec_per_km: float | None) -> str | None:
    if not sec_per_km or sec_per_km <= 0:
        return None
    m = int(sec_per_km // 60)
    s = int(round(sec_per_km % 60))
    return f"{m}:{s:02d} /km"


def _race_row(race: Race) -> dict:
    return {
        "id": race.id,
        "name": race.name,
        "date": race.date,
        "location": race.location,
        "distance_m": race.distance_m,
        "distance_km": round(race.distance_m / 1000, 2) if race.distance_m else None,
        "total_elevation_gain": race.total_elevation_gain,
        "total_elevation_loss": race.total_elevation_loss,
        "has_gpx": bool(race.gpx_raw),
        "elevation_profile": json.loads(race.elevation_profile_json) if race.elevation_profile_json else [],
        "latlng": json.loads(race.latlng_json) if race.latlng_json else [],
        "latlng_dist": json.loads(race.latlng_dist_json) if race.latlng_dist_json else [],
        "course_km_splits": json.loads(race.course_km_splits_json) if race.course_km_splits_json else [],
        "predicted_time_sec": race.predicted_time_sec,
        "predicted_time_str": _fmt_time(race.predicted_time_sec),
        "predicted_pace_sec_per_km": race.predicted_pace_sec_per_km,
        "predicted_pace_str": _fmt_pace(race.predicted_pace_sec_per_km),
        "race_plan": json.loads(race.race_plan_json) if race.race_plan_json else [],
        "prediction_breakdown": json.loads(race.prediction_breakdown_json) if race.prediction_breakdown_json else {},
        "plan_strategy": race.plan_strategy or "even",
        "linked_activity_id": race.linked_activity_id,
        "actual_time_sec": race.actual_time_sec,
        "actual_time_str": _fmt_time(race.actual_time_sec),
        "notes": race.notes,
        "aid_stations": json.loads(race.aid_stations_json) if race.aid_stations_json else [],
        "nutrition_settings": json.loads(race.nutrition_settings_json) if race.nutrition_settings_json else {
            "sweat_rate_ml_per_hr": 500,
            "cal_per_hr": 250,
            "carry_capacity_ml": 1500,
        },
        "created_at": race.created_at,
    }


async def _run_prediction(db: AsyncSession, race: Race, athlete_id: int, strategy: str | None = None):
    """Run prediction + plan generation and update race in-place."""
    from app.analytics.race_predictor import generate_race_plan, predict_race_time
    from app.config import get_settings

    if not race.distance_m or race.distance_m <= 0:
        return

    settings = get_settings()
    km_splits = json.loads(race.course_km_splits_json) if race.course_km_splits_json else []
    strat = strategy or race.plan_strategy or "even"

    pred = await predict_race_time(
        db,
        athlete_id=athlete_id,
        distance_m=race.distance_m,
        course_km_splits=km_splits,
        race_date=race.date,
        settings=settings,
    )

    race.predicted_time_sec = pred.get("predicted_time_sec")
    race.predicted_pace_sec_per_km = pred.get("predicted_pace_sec_per_km")
    race.prediction_breakdown_json = json.dumps(pred)
    race.plan_strategy = strat

    if race.predicted_time_sec and km_splits:
        plan = generate_race_plan(race.predicted_time_sec, km_splits, strat)
        race.race_plan_json = json.dumps(plan)

    race.updated_at = int(time.time())


@router.get("")
async def list_races(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race)
        .where(Race.athlete_id == athlete_id)
        .order_by(Race.date)
    )
    races = result.scalars().all()
    today = __import__("datetime").date.today().isoformat()
    upcoming = [_race_row(r) for r in races if r.date >= today]
    past = [_race_row(r) for r in races if r.date < today]
    return {"upcoming": upcoming, "past": past}


@router.post("")
async def create_race(
    request: Request,
    name: str = Form(...),
    date: str = Form(...),
    location: str = Form(""),
    distance_m: float | None = Form(None),
    strategy: str = Form("even"),
    notes: str = Form(""),
    gpx_file: UploadFile | None = File(None),
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)

    race = Race(
        athlete_id=athlete_id,
        name=name,
        date=date,
        location=location or None,
        distance_m=distance_m,
        plan_strategy=strategy,
        notes=notes or None,
        created_at=int(time.time()),
        updated_at=int(time.time()),
    )

    if gpx_file and gpx_file.filename:
        gpx_bytes = await gpx_file.read()
        try:
            gpx_xml = gpx_bytes.decode("utf-8", errors="replace")
            from app.analytics.gpx_parser import parse_gpx
            parsed = parse_gpx(gpx_xml)
            race.gpx_raw = gpx_xml
            race.distance_m = parsed["distance_m"]
            race.total_elevation_gain = parsed["total_elevation_gain"]
            race.total_elevation_loss = parsed["total_elevation_loss"]
            race.elevation_profile_json = json.dumps(parsed["elevation_profile"])
            race.latlng_json = json.dumps(parsed["latlng"])
            race.latlng_dist_json = json.dumps(parsed.get("latlng_cum_dist"))
            race.course_km_splits_json = json.dumps(parsed["km_splits"])
            waypoints = parsed.get("waypoints", [])
            if waypoints:
                race.aid_stations_json = json.dumps(waypoints)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"GPX parse error: {e}")

    db.add(race)
    await db.flush()

    # Run prediction immediately after creation — wrapped so GPX data is always committed
    if race.distance_m and race.distance_m > 0:
        try:
            await _run_prediction(db, race, athlete_id, strategy)
        except Exception as exc:
            import logging
            logging.getLogger("routes_races").warning("Prediction failed on race create: %s", exc)
            # Prediction failure is non-fatal; GPX/distance data is still saved

    return _race_row(race)


@router.get("/{race_id}")
async def get_race(
    race_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)
    return _race_row(race)


class RaceUpdateRequest(BaseModel):
    name: str | None = None
    date: str | None = None
    location: str | None = None
    notes: str | None = None
    linked_activity_id: int | None = None
    actual_time_sec: int | None = None
    plan_strategy: str | None = None
    nutrition_settings: dict | None = None


@router.patch("/{race_id}")
async def update_race(
    race_id: int,
    body: RaceUpdateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    if body.name is not None:
        race.name = body.name
    if body.date is not None:
        race.date = body.date
    if body.location is not None:
        race.location = body.location
    if body.notes is not None:
        race.notes = body.notes
    if body.linked_activity_id is not None:
        race.linked_activity_id = body.linked_activity_id
    if body.actual_time_sec is not None:
        race.actual_time_sec = body.actual_time_sec
    if body.plan_strategy is not None:
        race.plan_strategy = body.plan_strategy
    if body.nutrition_settings is not None:
        race.nutrition_settings_json = json.dumps(body.nutrition_settings)

    race.updated_at = int(time.time())
    await db.flush()
    return _race_row(race)


class PredictRequest(BaseModel):
    strategy: str | None = None


@router.post("/{race_id}/predict")
async def recalculate_prediction(
    race_id: int,
    body: PredictRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    import logging
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    if not race.distance_m or race.distance_m <= 0:
        raise HTTPException(status_code=422, detail="Race has no distance — add a GPX file or set distance first")

    # Re-parse stored GPX so existing races automatically upgrade to new segmentation
    if race.gpx_raw:
        from app.analytics.gpx_parser import parse_gpx
        try:
            parsed = parse_gpx(race.gpx_raw)
            race.distance_m = parsed["distance_m"]
            race.total_elevation_gain = parsed["total_elevation_gain"]
            race.total_elevation_loss = parsed["total_elevation_loss"]
            race.elevation_profile_json = json.dumps(parsed["elevation_profile"])
            race.latlng_json = json.dumps(parsed["latlng"])
            race.latlng_dist_json = json.dumps(parsed.get("latlng_cum_dist"))
            race.course_km_splits_json = json.dumps(parsed["km_splits"])
            # Merge GPX waypoint coordinates into existing aid stations.
            # Preserves user-edited distance_km values while adding/updating lat/lon.
            if parsed.get("waypoints"):
                new_wpts = {w["name"]: w for w in parsed["waypoints"]}
                existing = json.loads(race.aid_stations_json) if race.aid_stations_json else []
                if existing:
                    for station in existing:
                        match = new_wpts.get(station["name"])
                        if match:
                            station["lat"] = match["lat"]
                            station["lon"] = match["lon"]
                            # Don't overwrite user-edited distance_km
                    race.aid_stations_json = json.dumps(existing)
                else:
                    race.aid_stations_json = json.dumps(parsed["waypoints"])
            await db.flush()
        except Exception as exc:
            logging.getLogger("routes_races").warning("GPX re-parse failed: %s", exc)

    await _run_prediction(db, race, athlete_id, body.strategy)
    await db.flush()
    return _race_row(race)


class AidStationsRequest(BaseModel):
    stations: list  # [{name, distance_km, notes?}, ...]


@router.put("/{race_id}/aid_stations")
async def set_aid_stations(
    race_id: int,
    body: AidStationsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    # Validate and normalise each station. Preserves optional fields:
    #   lat/lon — map pin coordinates
    #   has_water, has_food, has_bags — booleans used by the nutrition planner
    clean = []
    for s in body.stations:
        name = str(s.get("name", "")).strip()
        try:
            dist = float(s["distance_km"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Each aid station must have a numeric distance_km")
        if not name:
            raise HTTPException(status_code=422, detail="Aid station name cannot be empty")
        entry: dict = {
            "name": name,
            "distance_km": round(dist, 2),
            "notes": str(s.get("notes", "") or "").strip(),
            # Default True for water — most race aid stations have water.
            # User can uncheck explicitly. Food/bags default False.
            "has_water": bool(s.get("has_water", True)),
            "has_food": bool(s.get("has_food", False)),
            "has_bags": bool(s.get("has_bags", False)),
        }
        if s.get("lat") is not None:
            try:
                entry["lat"] = float(s["lat"])
            except (TypeError, ValueError):
                pass
        if s.get("lon") is not None:
            try:
                entry["lon"] = float(s["lon"])
            except (TypeError, ValueError):
                pass
        clean.append(entry)

    # Sort by distance so they're always in course order
    clean.sort(key=lambda s: s["distance_km"])

    race.aid_stations_json = json.dumps(clean)
    race.updated_at = int(time.time())
    await db.flush()
    return _race_row(race)


@router.get("/{race_id}/strategies")
async def get_strategy_comparison(
    race_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Compare all three race strategies side-by-side (read-only, no DB write)."""
    from app.analytics.race_predictor import generate_all_strategies

    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    if not race.predicted_time_sec or not race.course_km_splits_json:
        raise HTTPException(status_code=422, detail="Race needs a prediction and GPX course data first")

    km_splits = json.loads(race.course_km_splits_json)
    return generate_all_strategies(race.predicted_time_sec, km_splits, race.distance_m)


@router.get("/{race_id}/prediction_history")
async def get_prediction_history(
    race_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    from app.analytics.race_predictor import compute_prediction_history
    from app.config import get_settings

    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    course_km_splits = json.loads(race.course_km_splits_json) if race.course_km_splits_json else []
    settings = get_settings()

    return await compute_prediction_history(
        db=db,
        athlete_id=athlete_id,
        distance_m=race.distance_m or 0,
        course_km_splits=course_km_splits,
        race_date=race.date,
        settings=settings,
    )


@router.get("/{race_id}/readiness")
async def get_race_readiness(
    race_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """7-day health trend leading into a race + overall assessment."""
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)

    from app.analytics.race_predictor import get_pre_race_health
    health = await get_pre_race_health(db, athlete_id, race.date, days=7)
    if not health:
        return {"available": False}

    # Daily trends for sparklines
    from datetime import date as _date
    from datetime import timedelta

    from app.models.schema import GarminDailyHealth
    race_d = _date.fromisoformat(race.date[:10])
    start_d = (race_d - timedelta(days=7)).isoformat()
    end_d = (race_d - timedelta(days=1)).isoformat()
    daily_result = await db.execute(
        select(GarminDailyHealth).where(
            GarminDailyHealth.athlete_id == athlete_id,
            GarminDailyHealth.date >= start_d,
            GarminDailyHealth.date <= end_d,
        ).order_by(GarminDailyHealth.date)
    )
    daily = daily_result.scalars().all()

    # Assessment
    concerns = 0
    advice = []
    readiness = health.get("avg_training_readiness")
    sleep = health.get("avg_sleep_score")
    battery = health.get("avg_body_battery")
    hrv_status = health.get("hrv_status_mode")

    if readiness is not None and readiness < 50:
        concerns += 1
        advice.append("Training readiness is below average — reduce intensity this week")
    if sleep is not None and sleep < 60:
        concerns += 1
        advice.append("Sleep quality has been low — prioritize rest before race day")
    if battery is not None and battery < 40:
        concerns += 1
        advice.append("Body battery is depleted — avoid hard sessions")
    if hrv_status == "LOW":
        concerns += 1
        advice.append("HRV is below baseline — your body may need more recovery")

    if concerns == 0:
        assessment = "Race Ready"
        assessment_color = "green"
        if not advice:
            advice.append("Health metrics look good — you're well prepared")
    elif concerns == 1:
        assessment = "Caution"
        assessment_color = "yellow"
    else:
        assessment = "Not Ideal"
        assessment_color = "red"

    return {
        "available": True,
        "assessment": assessment,
        "assessment_color": assessment_color,
        "advice": advice,
        "health_summary": health,
        "daily_trends": {
            "dates": [d.date for d in daily],
            "hrv": [d.hrv_last_night for d in daily],
            "sleep_hours": [round(d.sleep_duration_sec / 3600, 1) if d.sleep_duration_sec else None for d in daily],
            "body_battery": [d.body_battery_latest for d in daily],
            "training_readiness": [d.training_readiness for d in daily],
        },
    }


@router.delete("/{race_id}", status_code=204)
async def delete_race(
    race_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    athlete_id = _get_athlete_id(request)
    result = await db.execute(
        select(Race).where(Race.id == race_id, Race.athlete_id == athlete_id)
    )
    race = result.scalar_one_or_none()
    if not race:
        raise HTTPException(status_code=404)
    await db.delete(race)

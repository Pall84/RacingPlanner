import time

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Athlete(Base):
    __tablename__ = "athlete"

    id = Column(BigInteger, primary_key=True)  # Strava athlete ID
    username = Column(Text)
    firstname = Column(Text)
    lastname = Column(Text)
    city = Column(Text)
    country = Column(Text)
    sex = Column(String(1))
    premium = Column(Integer, default=0)
    profile_pic = Column(Text)
    weight = Column(Float)
    date_of_birth = Column(Text)
    height_cm = Column(Float)

    max_hr = Column(Integer)
    resting_hr = Column(Integer)
    ftp_watts = Column(Float)
    hr_zone_method = Column(Text)
    trimp_gender = Column(Text)

    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    token_expires = Column(BigInteger, nullable=False)

    # Multi-user additions
    is_admin = Column(Boolean, default=False, nullable=False)
    garmin_key_salt = Column(LargeBinary, nullable=True)

    created_at = Column(BigInteger, default=lambda: int(time.time()))
    updated_at = Column(BigInteger, default=lambda: int(time.time()), onupdate=lambda: int(time.time()))

    activities = relationship("Activity", back_populates="athlete", lazy="dynamic")


class Invite(Base):
    __tablename__ = "invites"

    code = Column(Text, primary_key=True)
    created_by_athlete_id = Column(BigInteger, ForeignKey("athlete.id"), nullable=True)
    email_hint = Column(Text, nullable=True)
    used_by_athlete_id = Column(BigInteger, ForeignKey("athlete.id"), nullable=True)
    created_at = Column(BigInteger, default=lambda: int(time.time()), nullable=False)
    used_at = Column(BigInteger, nullable=True)
    expires_at = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("idx_invites_unused", "used_by_athlete_id"),
    )


class Activity(Base):
    __tablename__ = "activities"

    id = Column(BigInteger, primary_key=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), nullable=False)
    name = Column(Text)
    type = Column(Text)
    sport_type = Column(Text)
    start_date = Column(Text, nullable=False)
    start_date_local = Column(Text, nullable=False)
    timezone = Column(Text)

    distance = Column(Float)
    moving_time = Column(Integer)
    elapsed_time = Column(Integer)
    total_elevation_gain = Column(Float)
    elev_low = Column(Float)
    elev_high = Column(Float)

    average_speed = Column(Float)
    max_speed = Column(Float)
    average_heartrate = Column(Float)
    max_heartrate = Column(Float)
    average_cadence = Column(Float)
    average_watts = Column(Float)
    max_watts = Column(Float)
    weighted_average_watts = Column(Float)

    suffer_score = Column(Integer)
    trainer = Column(Integer, default=0)
    commute = Column(Integer, default=0)
    manual = Column(Integer, default=0)
    has_heartrate = Column(Integer, default=0)
    has_kudos = Column(Integer, default=0)
    kudos_count = Column(Integer, default=0)

    map_summary_polyline = Column(Text)

    streams_synced = Column(Integer, default=0)
    metrics_computed = Column(Integer, default=0)
    laps_synced = Column(Integer, default=0)
    treadmill_corrected = Column(Integer, default=0)
    # User-marked race flag. Preserved across Strava re-sync / refresh.
    # Treated by the predictor as ground-truth race performance at this
    # distance — much stronger than the keyword-based auto-classification
    # that fills ActivityMetrics.workout_type.
    is_race = Column(Integer, default=0, nullable=False)

    raw_json = Column(Text)
    created_at = Column(BigInteger, default=lambda: int(time.time()))

    athlete = relationship("Athlete", back_populates="activities")
    streams = relationship("ActivityStream", back_populates="activity", cascade="all, delete-orphan")
    metrics = relationship(
        "ActivityMetrics", back_populates="activity", uselist=False, cascade="all, delete-orphan"
    )
    km_splits = relationship(
        "KmSplit", back_populates="activity", cascade="all, delete-orphan", order_by="KmSplit.km_index"
    )
    laps = relationship(
        "Lap", back_populates="activity", cascade="all, delete-orphan", order_by="Lap.lap_index"
    )

    __table_args__ = (
        Index("idx_activities_start_date", "start_date"),
        Index("idx_activities_type", "type"),
        Index("idx_activities_athlete", "athlete_id"),
        Index(
            "idx_activities_athlete_start",
            "athlete_id",
            "start_date",
            postgresql_using="btree",
        ),
    )


class ActivityStream(Base):
    __tablename__ = "activity_streams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id"), nullable=False)
    stream_type = Column(Text, nullable=False)
    data_json = Column(Text, nullable=False)
    resolution = Column(Text)
    series_type = Column(Text)
    created_at = Column(BigInteger, default=lambda: int(time.time()))

    activity = relationship("Activity", back_populates="streams")

    __table_args__ = (
        UniqueConstraint("activity_id", "stream_type", name="uq_stream_activity_type"),
        Index("idx_streams_activity", "activity_id"),
    )


class ActivityMetrics(Base):
    __tablename__ = "activity_metrics"

    activity_id = Column(BigInteger, ForeignKey("activities.id"), primary_key=True)

    avg_pace_sec_per_km = Column(Float)
    best_pace_sec_per_km = Column(Float)
    avg_gap_sec_per_km = Column(Float)

    ef_first_half = Column(Float)
    ef_second_half = Column(Float)
    pace_decoupling_pct = Column(Float)

    cadence_avg = Column(Float)
    cadence_min = Column(Float)
    cadence_max = Column(Float)
    cadence_cv_pct = Column(Float)

    stride_length_avg_m = Column(Float)
    stride_length_cv_pct = Column(Float)

    z1_seconds = Column(Integer)
    z2_seconds = Column(Integer)
    z3_seconds = Column(Integer)
    z4_seconds = Column(Integer)
    z5_seconds = Column(Integer)

    trimp_total = Column(Float)
    trimp_z1 = Column(Float)
    trimp_z2 = Column(Float)
    trimp_z3 = Column(Float)
    trimp_z4 = Column(Float)
    trimp_z5 = Column(Float)

    rss = Column(Float)

    normalized_power = Column(Float)
    intensity_factor = Column(Float)

    pacing_cv_pct = Column(Float)
    moving_elapsed_ratio = Column(Float)

    total_elevation_loss = Column(Float)

    workout_type = Column(Text)
    estimated_vdot = Column(Float)

    computed_at = Column(BigInteger, default=lambda: int(time.time()))

    activity = relationship("Activity", back_populates="metrics")


class KmSplit(Base):
    __tablename__ = "km_splits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id"), nullable=False)
    km_index = Column(Integer, nullable=False)

    distance_m = Column(Float)
    duration_sec = Column(Float)
    pace_sec_per_km = Column(Float)
    gap_sec_per_km = Column(Float)
    avg_hr = Column(Float)
    avg_cadence = Column(Float)
    elevation_gain = Column(Float)
    elevation_loss = Column(Float)
    avg_grade_pct = Column(Float)

    activity = relationship("Activity", back_populates="km_splits")

    __table_args__ = (
        UniqueConstraint("activity_id", "km_index", name="uq_km_split"),
        Index("idx_splits_activity", "activity_id"),
    )


class Lap(Base):
    __tablename__ = "laps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id"), nullable=False)
    strava_lap_id = Column(BigInteger)
    lap_index = Column(Integer)
    name = Column(Text)

    distance = Column(Float)
    moving_time = Column(Integer)
    elapsed_time = Column(Integer)
    average_speed = Column(Float)
    max_speed = Column(Float)
    average_heartrate = Column(Float)
    max_heartrate = Column(Float)
    average_cadence = Column(Float)
    total_elevation_gain = Column(Float)
    pace_sec_per_km = Column(Float)
    gap_sec_per_km = Column(Float)
    split_type = Column(Integer)  # Strava's km-split index (1,2,3,…); int in their API

    corrected_distance = Column(Float)
    corrected_elevation_gain = Column(Float)

    activity = relationship("Activity", back_populates="laps")

    __table_args__ = (Index("idx_laps_activity", "activity_id"),)


class DailyFitness(Base):
    __tablename__ = "daily_fitness"

    date = Column(Text, primary_key=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), primary_key=True)
    daily_rss = Column(Float, default=0.0)
    ctl = Column(Float)
    atl = Column(Float)
    tsb = Column(Float)
    updated_at = Column(BigInteger, default=lambda: int(time.time()), onupdate=lambda: int(time.time()))

    __table_args__ = (Index("idx_daily_fitness_athlete", "athlete_id"),)


class WeeklySummary(Base):
    __tablename__ = "weekly_summary"

    week_start = Column(Text, primary_key=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), primary_key=True)

    run_count = Column(Integer, default=0)
    total_distance = Column(Float, default=0.0)
    total_time = Column(Integer, default=0)
    total_elevation = Column(Float, default=0.0)
    total_rss = Column(Float, default=0.0)

    avg_ctl = Column(Float)
    training_monotony = Column(Float)
    training_strain = Column(Float)
    avg_ef = Column(Float)


class PersonalRecord(Base):
    __tablename__ = "personal_records"

    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), primary_key=True)
    record_type = Column(Text, primary_key=True)
    activity_id = Column(BigInteger, ForeignKey("activities.id"))
    value = Column(Float, nullable=False)
    date = Column(Text, nullable=False)


class Race(Base):
    __tablename__ = "races"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), nullable=False)
    name = Column(Text, nullable=False)
    date = Column(Text, nullable=False)
    location = Column(Text)
    distance_m = Column(Float)
    total_elevation_gain = Column(Float)
    total_elevation_loss = Column(Float)

    gpx_raw = Column(Text)
    elevation_profile_json = Column(Text)
    course_km_splits_json = Column(Text)
    latlng_json = Column(Text)
    latlng_dist_json = Column(Text)

    predicted_time_sec = Column(Float)
    predicted_pace_sec_per_km = Column(Float)
    race_plan_json = Column(Text)
    prediction_breakdown_json = Column(Text)
    plan_strategy = Column(Text, default="even")
    aid_stations_json = Column(Text)
    nutrition_settings_json = Column(Text)

    linked_activity_id = Column(BigInteger, ForeignKey("activities.id"), nullable=True)
    actual_time_sec = Column(Integer)
    notes = Column(Text)

    created_at = Column(BigInteger, default=lambda: int(time.time()))
    updated_at = Column(BigInteger, default=lambda: int(time.time()))

    __table_args__ = (Index("idx_races_athlete", "athlete_id"),)


class GarminCredentials(Base):
    __tablename__ = "garmin_credentials"

    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), primary_key=True)
    email_encrypted = Column(Text, nullable=False)
    password_encrypted = Column(Text, nullable=False)
    is_connected = Column(Integer, default=1)
    last_sync_date = Column(Text)
    last_error = Column(Text)
    created_at = Column(BigInteger, default=lambda: int(time.time()))
    updated_at = Column(BigInteger, default=lambda: int(time.time()))


class GarminDailyHealth(Base):
    __tablename__ = "garmin_daily_health"

    date = Column(Text, primary_key=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), primary_key=True)

    hrv_weekly_avg = Column(Float)
    hrv_last_night = Column(Float)
    hrv_status = Column(Text)

    sleep_duration_sec = Column(Integer)
    sleep_score = Column(Float)
    sleep_deep_sec = Column(Integer)
    sleep_light_sec = Column(Integer)
    sleep_rem_sec = Column(Integer)
    sleep_awake_sec = Column(Integer)

    resting_hr = Column(Integer)
    body_battery_high = Column(Integer)
    body_battery_low = Column(Integer)
    body_battery_latest = Column(Integer)
    stress_avg = Column(Integer)
    training_readiness = Column(Float)
    training_status = Column(Text)

    vo2max_running = Column(Float)

    # Lactate threshold — from Garmin's /latestLactateThreshold endpoint.
    # These change slowly; populated on the most recent day of each sync
    # (null on older rows is expected and fine).
    lactate_threshold_speed_ms = Column(Float)   # m/s
    lactate_threshold_hr = Column(Integer)       # bpm
    # Endurance score — Garmin's 0-100 aerobic durability metric.
    endurance_score = Column(Float)

    raw_json = Column(Text)
    updated_at = Column(BigInteger, default=lambda: int(time.time()))

    __table_args__ = (Index("idx_garmin_health_athlete", "athlete_id"),)


class AthleteGoal(Base):
    __tablename__ = "athlete_goals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    athlete_id = Column(BigInteger, ForeignKey("athlete.id"), nullable=False)
    goal_type = Column(Text, nullable=False)
    target_value = Column(Float, nullable=False)
    target_unit = Column(Text)
    target_date = Column(Text)
    race_id = Column(BigInteger, ForeignKey("races.id"), nullable=True)
    created_at = Column(BigInteger, default=lambda: int(time.time()))

    __table_args__ = (Index("idx_goals_athlete", "athlete_id"),)

from functools import lru_cache
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _empty_to_none(v):
    if isinstance(v, str) and v.strip() == "":
        return None
    return v


OptionalInt = Annotated[int | None, BeforeValidator(_empty_to_none)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql+asyncpg://racing:racing@localhost:5432/racingplanner"
    )

    frontend_origin: str = "http://localhost:5173"

    session_secret: str = Field(default="dev-only-insecure-change-me")

    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/auth/callback"

    garmin_master_key: str = ""

    admin_athlete_id: OptionalInt = None

    sentry_dsn: str = ""

    # --- Training defaults (used when athlete has not customized) ---
    max_hr: int = 190
    resting_hr: int = 50
    ftp_watts: float = 250.0
    hr_zone_method: str = "karvonen"
    trimp_gender: str = "male"

    # --- Fitness model constants ---
    ctl_days: int = 42
    atl_days: int = 7

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return not self.frontend_origin.startswith("http://localhost")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_athlete_settings(athlete) -> Settings:
    """Return a Settings instance with the athlete's per-user overrides applied.

    Any field left null/zero on the athlete row falls back to the env default.
    """
    base = get_settings()
    overrides: dict = {}
    if athlete.max_hr:
        overrides["max_hr"] = athlete.max_hr
    if athlete.resting_hr:
        overrides["resting_hr"] = athlete.resting_hr
    if athlete.ftp_watts:
        overrides["ftp_watts"] = athlete.ftp_watts
    if athlete.hr_zone_method:
        overrides["hr_zone_method"] = athlete.hr_zone_method
    if athlete.trimp_gender:
        overrides["trimp_gender"] = athlete.trimp_gender
    if not overrides:
        return base
    return base.model_copy(update=overrides)

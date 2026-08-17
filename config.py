from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 3032


class AppConfig(BaseModel):
    default_year: int = 2026
    server: ServerConfig = ServerConfig()


class CacheConfig(BaseModel):
    html_directory: str = "html"
    csv_directory: str = "csv"


class TorConfig(BaseModel):
    socks_port: int = 9050
    control_port: int = 9051


class ScrapingConfig(BaseModel):
    cache: CacheConfig = CacheConfig()
    tor: TorConfig = TorConfig()


class UrlsConfig(BaseModel):
    calendar_base: str = "https://play.usaultimate.org/events/tournament/"
    college_schedule: str = "https://usaultimate.org/college/schedule/"
    club_schedule: str = "https://usaultimate.org/club/schedule/"


class SchedulerConfig(BaseModel):
    calendar_interval_hours: int = 8
    ongoing_interval_minutes: int = 10
    ongoing_team_refresh_interval_hours: int = 12
    upcoming_interval_hours: int = 12
    recently_ended_interval_hours: int = 8
    videos_interval_hours: int = 24
    # WFDF equivalents of the USAU intervals above (see the WFDF source
    # task: reference/games refetch on the "ongoing" cadence, rosters on
    # the slower "roster refresh" cadence -- same shape as USAU's
    # ongoing_interval_minutes / ongoing_team_refresh_interval_hours).
    wfdf_ongoing_interval_minutes: int = 10
    wfdf_roster_refresh_interval_hours: int = 12
    wfdf_upcoming_interval_hours: int = 12


class Config(BaseModel):
    app: AppConfig = AppConfig()
    scraping: ScrapingConfig = ScrapingConfig()
    season_ids: Dict[int, int] = {}
    urls: UrlsConfig = UrlsConfig()
    scheduler: SchedulerConfig = SchedulerConfig()
    divisions: Dict[str, str] = {}


class SecretsConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_url: Optional[str] = None
    # Shared secret for POST /v2/ingest (sent as X-Ingest-Token). Reads
    # INGEST_TOKEN from .env or the environment, like every other secret here.
    ingest_token: Optional[str] = None
    youtube_api_key: Optional[str] = None
    vimeo_client_id: Optional[str] = None
    vimeo_client_secret: Optional[str] = None
    vimeo_access_token: Optional[str] = None
    commit_and_push: bool = False
    post_to_api: bool = False
    load_cal_on_start: bool = False
    host: str = "0.0.0.0"


def load_config(config_path: str = "config.yaml") -> Config:
    """Load configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        return Config()

    with open(path, "r") as f:
        data = yaml.safe_load(f)

    return Config(**data)


@lru_cache
def get_config() -> Config:
    """Get cached configuration singleton."""
    return load_config()


@lru_cache
def get_secrets() -> SecretsConfig:
    """Get cached secrets singleton."""
    return SecretsConfig()


def get_season_id(year: int) -> Optional[int]:
    """Get season ID for a given year."""
    return get_config().season_ids.get(year)


def get_division_path(name: str) -> Optional[str]:
    """Get division path for a given division name."""
    return get_config().divisions.get(name)

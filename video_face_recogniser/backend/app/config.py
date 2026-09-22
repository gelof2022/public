from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_version() -> str:
    app_directory = Path(__file__).resolve().parents[1]
    for candidate in (app_directory / "VERSION", app_directory.parent / "VERSION"):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return "0.0.0+unknown"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Video Face Recogniser"
    app_version: str = project_version()
    database_url: str = "sqlite:///./development.db"
    redis_url: str = "redis://localhost:6379/0"
    stash_url: str = ""
    stash_api_key: str = ""
    stash_path_prefix: str = "/data"
    media_path_prefix: str = "/media"
    appdata_path: Path = Path("./appdata")
    cors_origins: str = "http://localhost:8787"
    log_level: str = "INFO"
    frame_scan_fps: float = Field(2.0, ge=0.5, le=10)
    frame_sample_interval_seconds: float = Field(30.0, gt=0)
    frame_scene_threshold: float = Field(0.35, gt=0, le=1)
    max_frames_per_video: int = Field(300, ge=2, le=3000)
    frame_max_width: int = 1920
    appearance_enabled: bool = True
    appearance_crop_side: float = Field(0.75, ge=0, le=2)
    appearance_crop_above: float = Field(0.25, ge=0, le=1)
    appearance_crop_below: float = Field(2.0, ge=0.5, le=4)
    grouping_assisted_min: float = Field(0.45, ge=0.25, le=0.7)
    grouping_appearance_min: float = Field(0.90, ge=0, le=1)
    grouping_assisted_seconds: float = Field(30, gt=0, le=300)
    grouping_duplicate_min: float = Field(0.97, ge=0.9, le=1)
    grouping_duplicate_seconds: float = Field(3, gt=0, le=10)
    processing_job_timeout_seconds: int = 21600

    @field_validator("stash_url")
    @classmethod
    def trim_stash_url(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

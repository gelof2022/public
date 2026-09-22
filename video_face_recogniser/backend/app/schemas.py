from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BatchState = Literal[
    "created", "queued", "processing", "video_processing_complete", "face_detection_complete", "clustering",
    "ready_for_review", "review_in_progress", "review_complete", "ready_to_commit",
    "committing", "committed", "failed",
]


class HealthResponse(BaseModel):
    status: str
    version: str
    database: str


class SettingsResponse(BaseModel):
    stash_url: str
    stash_api_key_configured: bool
    stash_path_prefix: str
    media_path_prefix: str
    appdata_path: str


class StashStatus(BaseModel):
    connected: bool
    version: str | None = None
    error: str | None = None


class SyncResult(BaseModel):
    imported: int
    updated: int
    skipped_without_files: int
    total_in_stash: int


class SceneResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    stash_scene_id: str
    title: str | None
    scene_date: date | None
    source_path: str
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_codec: str | None
    media_format: str | None
    audio_codec: str | None
    bit_rate: int | None
    file_size: int | None
    source_created_at: datetime | None
    source_modified_at: datetime | None
    probed_at: datetime | None
    media_metadata: dict[str, Any] | None
    thumbnail_url: str | None = None
    processed: bool = False
    frame_count: int = 0
    last_processed_at: datetime | None = None
    existing_tag_ids: list[str]


class SceneList(BaseModel):
    items: list[SceneResponse]
    total: int
    page: int
    per_page: int


class SceneIdList(BaseModel):
    ids: list[str]
    total: int


class SceneFailure(BaseModel):
    scene_id: str
    title: str | None
    source_path: str
    error: str


class BatchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    scene_ids: list[str] = Field(min_length=1)


class BatchResponse(BaseModel):
    archived: bool = False
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    state: BatchState
    selection: dict[str, Any]
    scene_count: int
    created_at: datetime
    updated_at: datetime
    progress_current: int = 0
    progress_total: int = 0
    frame_count: int = 0
    error: str | None = None
    latest_job_type: str | None = None
    latest_job_state: str | None = None
    failed_scene_count: int = 0
    errors: list[SceneFailure] = Field(default_factory=list)


class ProcessingJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    batch_id: str | None
    job_type: str
    state: str
    progress_current: int
    progress_total: int
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime


class FrameResponse(BaseModel):
    id: str
    scene_id: str
    timestamp_seconds: float
    url: str
    width: int | None
    height: int | None
    scene_duration_seconds: float | None = None
    scene_title: str | None = None
    scene_thumbnail_url: str | None = None
    video_url: str | None = None

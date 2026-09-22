import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from pgvector.sqlalchemy import Vector

EmbeddingVector = Vector(128).with_variant(JSON(none_as_null=True), "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Library(Base):
    __tablename__ = "libraries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), default="Home videos")
    stash_url: Mapped[str] = mapped_column(String(2048))
    stash_path_prefix: Mapped[str] = mapped_column(String(2048))
    media_path_prefix: Mapped[str] = mapped_column(String(2048))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    scenes: Mapped[list["StashScene"]] = relationship(back_populates="library", cascade="all, delete-orphan")


class StashScene(Base):
    __tablename__ = "stash_scenes"
    __table_args__ = (UniqueConstraint("library_id", "stash_scene_id", name="uq_scene_library_stash_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), index=True)
    stash_scene_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(1024))
    scene_date: Mapped[date | None] = mapped_column(Date)
    source_path: Mapped[str] = mapped_column(String(4096), index=True)
    stash_path: Mapped[str] = mapped_column(String(4096))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(128))
    media_format: Mapped[str | None] = mapped_column(String(128))
    audio_codec: Mapped[str | None] = mapped_column(String(128))
    bit_rate: Mapped[int | None] = mapped_column(BigInteger)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    thumbnail_url: Mapped[str | None] = mapped_column(String(4096))
    media_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict)
    existing_tag_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    stash_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    library: Mapped[Library] = relationship(back_populates="scenes")
    frames: Mapped[list["Frame"]] = relationship(back_populates="scene", cascade="all, delete-orphan")


class Batch(Base):
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(64), default="created", index=True)
    selection: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scenes: Mapped[list["BatchScene"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    jobs: Mapped[list["ProcessingJob"]] = relationship(back_populates="batch", cascade="all, delete-orphan")
    frames: Mapped[list["BatchFrame"]] = relationship(back_populates="batch", cascade="all, delete-orphan")


class BatchScene(Base):
    __tablename__ = "batch_scenes"

    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="RESTRICT"), primary_key=True)
    state: Mapped[str] = mapped_column(String(64), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    batch: Mapped[Batch] = relationship(back_populates="scenes")
    scene: Mapped[StashScene] = relationship()


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (
        UniqueConstraint(
            "scene_id", "source_fingerprint", "sampling_key", "timestamp_ms",
            name="uq_frame_source_sample_timestamp",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="CASCADE"), index=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    sampling_key: Mapped[str] = mapped_column(String(64))
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    relative_path: Mapped[str] = mapped_column(String(4096))
    selection_reason: Mapped[str] = mapped_column(String(64), default="adaptive")
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    scene: Mapped[StashScene] = relationship(back_populates="frames")
    batches: Mapped[list["BatchFrame"]] = relationship(back_populates="frame", cascade="all, delete-orphan")


class BatchFrame(Base):
    __tablename__ = "batch_frames"
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True)
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), primary_key=True)
    batch: Mapped[Batch] = relationship(back_populates="frames")
    frame: Mapped[Frame] = relationship(back_populates="batches")


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (UniqueConstraint("component", "name", "version", name="uq_model_component_name_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    component: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    version: Mapped[str] = mapped_column(String(255))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    batch: Mapped[Batch | None] = relationship(back_populates="jobs")


class FaceAnalysis(Base):
    __tablename__ = "face_analyses"
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    face_count: Mapped[int] = mapped_column(Integer)


class FaceObservation(Base):
    __tablename__ = "face_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    frame_id: Mapped[str] = mapped_column(ForeignKey("frames.id", ondelete="CASCADE"), index=True)
    model_key: Mapped[str] = mapped_column(String(64), index=True)
    bbox: Mapped[list[float]] = mapped_column(JSON)
    landmarks: Mapped[list[list[float]]] = mapped_column(JSON)
    detector_confidence: Mapped[float] = mapped_column(Float)
    quality: Mapped[float] = mapped_column(Float)
    usable: Mapped[bool] = mapped_column(Boolean)
    quality_details: Mapped[dict] = mapped_column(JSON)
    relative_path: Mapped[str] = mapped_column(String(4096))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FaceEmbedding(Base):
    __tablename__ = "face_embeddings"
    face_id: Mapped[str] = mapped_column(ForeignKey("face_observations.id", ondelete="CASCADE"), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    vector: Mapped[Any] = mapped_column(EmbeddingVector)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppearanceEmbedding(Base):
    __tablename__ = "appearance_embeddings"
    # Deliberately not attached to Person or canonical observations: review IDs survive re-detection.
    face_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    model_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="CASCADE"), index=True)
    input_key: Mapped[str] = mapped_column(String(64))
    vector: Mapped[Any | None] = mapped_column(Vector(1280).with_variant(JSON(none_as_null=True), "sqlite"), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FaceGrouping(Base):
    __tablename__ = "face_groupings"
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="CASCADE"), primary_key=True)
    input_key: Mapped[str] = mapped_column(String(64))
    model_key: Mapped[str] = mapped_column(String(64))
    algorithm_key: Mapped[str] = mapped_column(String(64))
    groups: Mapped[list] = mapped_column(JSON)
    diagnostics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Person(Base):
    __tablename__ = "people"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    name_key: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StashPerformerLink(Base):
    __tablename__ = "stash_performer_links"
    library_id: Mapped[str] = mapped_column(ForeignKey("libraries.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True)
    performer_id: Mapped[str] = mapped_column(String(64))


class ScenePerson(Base):
    __tablename__ = "scene_people"
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewEvidence(Base):
    __tablename__ = "review_evidence"
    # Snapshot survives detector regeneration and refers to the preserved crop.
    bbox: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="RESTRICT"), index=True)
    frame_id: Mapped[str] = mapped_column(String(36))
    timestamp_ms: Mapped[int] = mapped_column(Integer)
    relative_path: Mapped[str] = mapped_column(String(4096))
    quality: Mapped[float] = mapped_column(Float)
    vector: Mapped[Any | None] = mapped_column(EmbeddingVector, nullable=True)
    model_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReviewCluster(Base):
    __tablename__ = "review_clusters"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="RESTRICT"), index=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="RESTRICT"))
    member_ids: Mapped[list[str]] = mapped_column(JSON)
    confirmed_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"))
    state: Mapped[str] = mapped_column(String(32), default="pending")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    cluster_id: Mapped[str | None] = mapped_column(ForeignKey("review_clusters.id", ondelete="RESTRICT"))
    action: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RejectedSuggestion(Base):
    __tablename__ = "rejected_suggestions"
    cluster_id: Mapped[str] = mapped_column(ForeignKey("review_clusters.id", ondelete="CASCADE"), primary_key=True)
    person_id: Mapped[str] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SceneReview(Base):
    __tablename__ = "scene_reviews"
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="RESTRICT"), primary_key=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="RESTRICT"), primary_key=True)
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class SceneRedetection(Base):
    __tablename__ = "scene_redetections"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(ForeignKey("batches.id", ondelete="RESTRICT"), index=True)
    scene_id: Mapped[str] = mapped_column(ForeignKey("stash_scenes.id", ondelete="RESTRICT"))
    job_id: Mapped[str] = mapped_column(ForeignKey("processing_jobs.id", ondelete="RESTRICT"))
    threshold: Mapped[float] = mapped_column(Float)
    state: Mapped[str] = mapped_column(String(32), default="queued")
    baseline: Mapped[str] = mapped_column(String(64))
    frame_ids: Mapped[list[str]] = mapped_column(JSON)
    proposals: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

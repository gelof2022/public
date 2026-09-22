"""Phase one foundation schema."""

from alembic import op
import sqlalchemy as sa

revision = "0001_phase_one"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "libraries",
        sa.Column("id", sa.String(36), primary_key=True), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("stash_url", sa.String(2048), nullable=False),
        sa.Column("stash_path_prefix", sa.String(2048), nullable=False),
        sa.Column("media_path_prefix", sa.String(2048), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "stash_scenes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("library_id", sa.String(36), sa.ForeignKey("libraries.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stash_scene_id", sa.String(64), nullable=False), sa.Column("title", sa.String(1024)),
        sa.Column("scene_date", sa.Date), sa.Column("source_path", sa.String(4096), nullable=False),
        sa.Column("stash_path", sa.String(4096), nullable=False), sa.Column("file_size", sa.BigInteger),
        sa.Column("duration_seconds", sa.Float), sa.Column("width", sa.Integer), sa.Column("height", sa.Integer),
        sa.Column("frame_rate", sa.Float), sa.Column("video_codec", sa.String(128)),
        sa.Column("existing_tag_ids", sa.JSON(), nullable=False),
        sa.Column("stash_updated_at", sa.DateTime(timezone=True)),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("library_id", "stash_scene_id", name="uq_scene_library_stash_id"),
    )
    op.create_index("ix_stash_scenes_library_id", "stash_scenes", ["library_id"])
    op.create_index("ix_stash_scenes_source_path", "stash_scenes", ["source_path"])
    op.create_table(
        "batches", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("description", sa.Text),
        sa.Column("state", sa.String(64), nullable=False), sa.Column("selection", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_batches_state", "batches", ["state"])
    op.create_table(
        "batch_scenes",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("state", sa.String(64), nullable=False), sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_settings", sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_versions", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("component", sa.String(64), nullable=False), sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(255), nullable=False), sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("component", "name", "version", name="uq_model_component_name_version"),
    )
    op.create_table(
        "processing_jobs", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="CASCADE")),
        sa.Column("job_type", sa.String(64), nullable=False), sa.Column("state", sa.String(64), nullable=False),
        sa.Column("progress_current", sa.Integer, nullable=False), sa.Column("progress_total", sa.Integer, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False), sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_processing_jobs_state", "processing_jobs", ["state"])


def downgrade() -> None:
    op.drop_table("processing_jobs")
    op.drop_table("model_versions")
    op.drop_table("app_settings")
    op.drop_table("batch_scenes")
    op.drop_table("batches")
    op.drop_table("stash_scenes")
    op.drop_table("libraries")

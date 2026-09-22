"""Phase two video processing schema."""

from alembic import op
import sqlalchemy as sa

revision = "0002_phase_two"
down_revision = "0001_phase_one"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stash_scenes", sa.Column("source_fingerprint", sa.String(64)))
    op.add_column("stash_scenes", sa.Column("probed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_stash_scenes_source_fingerprint", "stash_scenes", ["source_fingerprint"])
    op.create_table(
        "frames",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("sampling_key", sa.String(64), nullable=False),
        sa.Column("timestamp_ms", sa.Integer, nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("selection_reason", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer), sa.Column("height", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "scene_id", "source_fingerprint", "sampling_key", "timestamp_ms",
            name="uq_frame_source_sample_timestamp",
        ),
    )
    op.create_index("ix_frames_scene_id", "frames", ["scene_id"])
    op.create_table(
        "batch_frames",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("frame_id", sa.String(36), sa.ForeignKey("frames.id", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("batch_frames")
    op.drop_table("frames")
    op.drop_index("ix_stash_scenes_source_fingerprint", table_name="stash_scenes")
    op.drop_column("stash_scenes", "probed_at")
    op.drop_column("stash_scenes", "source_fingerprint")

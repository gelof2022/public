"""Staged scene re-detection and durable face locations."""
from alembic import op
import sqlalchemy as sa

revision = "0009_scene_redetection"
down_revision = "0008_scene_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_evidence", sa.Column("bbox", sa.JSON(), nullable=True))
    op.execute("UPDATE review_evidence SET bbox = face_observations.bbox FROM face_observations WHERE review_evidence.id = face_observations.id")
    op.create_table("scene_redetections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("baseline", sa.String(64), nullable=False),
        sa.Column("frame_ids", sa.JSON, nullable=False),
        sa.Column("proposals", sa.JSON, nullable=False),
        sa.Column("summary", sa.JSON, nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_scene_redetections_batch_id", "scene_redetections", ["batch_id"])


def downgrade():
    op.drop_table("scene_redetections")
    op.drop_column("review_evidence", "bbox")

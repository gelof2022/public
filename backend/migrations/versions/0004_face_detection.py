"""Independent, versioned face detection and quality observations."""
from alembic import op
import sqlalchemy as sa

revision = "0004_face_detection"
down_revision = "0003_scene_details"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("face_analyses",
        sa.Column("frame_id", sa.String(36), sa.ForeignKey("frames.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model_key", sa.String(64), primary_key=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("face_count", sa.Integer, nullable=False))
    op.create_table("face_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("frame_id", sa.String(36), sa.ForeignKey("frames.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_key", sa.String(64), nullable=False),
        sa.Column("bbox", sa.JSON, nullable=False),
        sa.Column("landmarks", sa.JSON, nullable=False),
        sa.Column("detector_confidence", sa.Float, nullable=False),
        sa.Column("quality", sa.Float, nullable=False),
        sa.Column("usable", sa.Boolean, nullable=False),
        sa.Column("quality_details", sa.JSON, nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_face_observations_frame_id", "face_observations", ["frame_id"])
    op.create_index("ix_face_observations_model_key", "face_observations", ["model_key"])


def downgrade():
    op.drop_table("face_observations")
    op.drop_table("face_analyses")

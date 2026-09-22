"""Explicit per-batch scene completion, independent of face identity coverage."""
from alembic import op
import sqlalchemy as sa

revision = "0008_scene_review"
down_revision = "0007_face_confirmation"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("scene_reviews",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("complete", sa.Boolean, nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("scene_reviews")

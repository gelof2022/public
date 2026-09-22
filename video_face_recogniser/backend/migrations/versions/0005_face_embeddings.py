"""Versioned embeddings and reproducible within-video grouping."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0005_face_embeddings"
down_revision = "0004_face_detection"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("face_embeddings",
        sa.Column("face_id", sa.String(36), sa.ForeignKey("face_observations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("model_key", sa.String(64), primary_key=True),
        sa.Column("vector", Vector(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("face_groupings",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("input_key", sa.String(64), nullable=False),
        sa.Column("model_key", sa.String(64), nullable=False),
        sa.Column("algorithm_key", sa.String(64), nullable=False),
        sa.Column("groups", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("face_groupings")
    op.drop_table("face_embeddings")

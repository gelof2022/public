"""Durable manual review and identities, separate from automatic grouping."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "0006_review"
down_revision = "0005_face_embeddings"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("people",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("name_key", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("review_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("frame_id", sa.String(36), nullable=False),
        sa.Column("timestamp_ms", sa.Integer, nullable=False),
        sa.Column("relative_path", sa.String(4096), nullable=False),
        sa.Column("quality", sa.Float, nullable=False),
        sa.Column("vector", Vector(128)), sa.Column("model_key", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_review_evidence_scene_id", "review_evidence", ["scene_id"])
    op.create_table("review_clusters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("batches.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scene_id", sa.String(36), sa.ForeignKey("stash_scenes.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("member_ids", sa.JSON, nullable=False),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id", ondelete="RESTRICT")),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_review_clusters_batch_id", "review_clusters", ["batch_id"])
    op.create_table("review_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("review_clusters.id", ondelete="RESTRICT")),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("rejected_suggestions",
        sa.Column("cluster_id", sa.String(36), sa.ForeignKey("review_clusters.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("person_id", sa.String(36), sa.ForeignKey("people.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ["rejected_suggestions", "review_decisions", "review_clusters", "review_evidence", "people"]:
        op.drop_table(table)

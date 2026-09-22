"""Separate intra-video appearance cache and bounded grouping diagnostics."""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = '0010_appearance'
down_revision = '0009_scene_redetection'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('appearance_embeddings',
        sa.Column('face_id', sa.String(36), primary_key=True),
        sa.Column('model_key', sa.String(64), primary_key=True),
        sa.Column('scene_id', sa.String(36), sa.ForeignKey('stash_scenes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('input_key', sa.String(64), nullable=False),
        sa.Column('vector', Vector(1280), nullable=True),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_appearance_embeddings_scene_id', 'appearance_embeddings', ['scene_id'])
    op.add_column('face_groupings', sa.Column('diagnostics', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column('face_groupings', 'diagnostics')
    op.drop_table('appearance_embeddings')

"""Scene thumbnails and detailed media metadata."""

from alembic import op
import sqlalchemy as sa

revision = "0003_scene_details"
down_revision = "0002_phase_two"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stash_scenes", sa.Column("media_format", sa.String(128)))
    op.add_column("stash_scenes", sa.Column("audio_codec", sa.String(128)))
    op.add_column("stash_scenes", sa.Column("bit_rate", sa.BigInteger))
    op.add_column("stash_scenes", sa.Column("source_created_at", sa.DateTime(timezone=True)))
    op.add_column("stash_scenes", sa.Column("source_modified_at", sa.DateTime(timezone=True)))
    op.add_column("stash_scenes", sa.Column("thumbnail_url", sa.String(4096)))
    op.add_column("stash_scenes", sa.Column("media_metadata", sa.JSON))


def downgrade() -> None:
    op.drop_column("stash_scenes", "media_metadata")
    op.drop_column("stash_scenes", "thumbnail_url")
    op.drop_column("stash_scenes", "source_modified_at")
    op.drop_column("stash_scenes", "source_created_at")
    op.drop_column("stash_scenes", "bit_rate")
    op.drop_column("stash_scenes", "audio_codec")
    op.drop_column("stash_scenes", "media_format")

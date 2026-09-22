"""Remember local Person to Stash Performer mappings per library."""
from alembic import op
import sqlalchemy as sa

revision = '0012_stash_performers'
down_revision = '0011_scene_people'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('stash_performer_links',
        sa.Column('library_id',sa.String(36),sa.ForeignKey('libraries.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('person_id',sa.String(36),sa.ForeignKey('people.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('performer_id',sa.String(64),nullable=False))


def downgrade():
    op.drop_table('stash_performer_links')

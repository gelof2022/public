"""Manual scene presence without face reference evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0011_scene_people'
down_revision = '0010_appearance'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('scene_people',
        sa.Column('scene_id',sa.String(36),sa.ForeignKey('stash_scenes.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('person_id',sa.String(36),sa.ForeignKey('people.id',ondelete='RESTRICT'),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))


def downgrade():
    op.drop_table('scene_people')

"""Hide archived batches without deleting their review evidence."""
from alembic import op
import sqlalchemy as sa

revision = '0013_batch_archive'
down_revision = '0012_stash_performers'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('batches',sa.Column('archived',sa.Boolean(),nullable=False,server_default=sa.false()))


def downgrade():
    op.drop_column('batches','archived')

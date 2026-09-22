"""Track explicit face confirmations; legacy cluster labels require review."""
from alembic import op
import sqlalchemy as sa

revision = "0007_face_confirmation"
down_revision = "0006_review"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("review_clusters", sa.Column("confirmed_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))


def downgrade():
    op.drop_column("review_clusters", "confirmed_ids")

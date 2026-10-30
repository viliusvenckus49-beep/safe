"""Independent public contact; preserve every catalog and historical application row."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("merchant_entries", sa.Column("contact", sa.String(200), nullable=True))


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM merchant_entries WHERE contact IS NOT NULL"))
        .scalar()
    ):
        raise RuntimeError("Preserve merchant contacts; use a reviewed forward migration")
    op.drop_column("merchant_entries", "contact")

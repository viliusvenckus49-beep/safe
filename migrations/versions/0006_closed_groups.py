"""Require explicit owner approval while preserving existing managed groups."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("managed_groups") as batch:
        batch.add_column(
            sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.true())
        )
    with op.batch_alter_table("managed_groups") as batch:
        batch.alter_column("approved", existing_type=sa.Boolean(), server_default=sa.false())


def downgrade():
    with op.batch_alter_table("managed_groups") as batch:
        batch.drop_column("approved")

"""Add optional per-user locale without changing existing business data."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("language", sa.String(2), nullable=True))
        batch.create_check_constraint(
            "ck_user_language", "language IS NULL OR language IN ('lt','en','ru')"
        )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("ck_user_language", type_="check")
        batch.drop_column("language")

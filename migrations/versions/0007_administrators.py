"""Persist administrator grants/revocations and immutable decision history."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "administrators",
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("assigned_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("telegram_id > 0", name="ck_administrator_id"),
    )
    op.create_table(
        "administrator_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "telegram_id",
            sa.BigInteger(),
            sa.ForeignKey("administrators.telegram_id"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_administrator_changes_telegram_id", "administrator_changes", ["telegram_id"]
    )


def downgrade():
    op.drop_index("ix_administrator_changes_telegram_id", table_name="administrator_changes")
    op.drop_table("administrator_changes")
    op.drop_table("administrators")

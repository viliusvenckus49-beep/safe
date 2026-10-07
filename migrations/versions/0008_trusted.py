"""Auditable manual TRUSTED designations; TOP status is calculated dynamically."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "trusted_designations",
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "trusted_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trusted_actions_target_id", "trusted_actions", ["target_id"])


def downgrade():
    op.drop_index("ix_trusted_actions_target_id", table_name="trusted_actions")
    op.drop_table("trusted_actions")
    op.drop_table("trusted_designations")

"""Add actual activity ledger without changing existing records."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "profile_activity",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("message_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("active_date", sa.Date(), nullable=False),
    )
    op.create_index("ix_profile_activity_user_id", "profile_activity", ["user_id"])


def downgrade():
    op.drop_index("ix_profile_activity_user_id", table_name="profile_activity")
    op.drop_table("profile_activity")

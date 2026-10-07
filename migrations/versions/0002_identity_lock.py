"""Serialize username ownership observations across concurrent workers.

Revision ID: 0002_identity_lock
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_identity_lock"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "identity_lock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_identity_lock_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(table, [{"id": 1}])


def downgrade():
    op.drop_table("identity_lock")

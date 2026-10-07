"""Store required comments on new REP requests; preserve legacy rows with NULL."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table(
        "reputation_requests",
        table_args=(
            sa.CheckConstraint("value IN (-1,1)"),
            sa.CheckConstraint("giver_user_id != receiver_user_id"),
            sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')"),
        ),
    ) as batch:
        batch.add_column(sa.Column("comment", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_rep_comment", "comment IS NULL OR length(trim(comment)) BETWEEN 5 AND 1500"
        )


def downgrade():
    with op.batch_alter_table(
        "reputation_requests",
        table_args=(
            sa.CheckConstraint("value IN (-1,1)"),
            sa.CheckConstraint("giver_user_id != receiver_user_id"),
            sa.CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')"),
        ),
    ) as batch:
        batch.drop_constraint("ck_rep_comment", type_="check")
        batch.drop_column("comment")

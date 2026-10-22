"""Independent word filters and durable per-member KICK rejoin escalation."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    for table, columns in (
        (
            "group_check_settings",
            [
                sa.Column("banned_words", sa.JSON(), nullable=False, server_default="[]"),
                sa.Column("words_action", sa.String(8), nullable=False, server_default="OFF"),
                sa.Column(
                    "words_delete", sa.Boolean(), nullable=False, server_default=sa.text("true")
                ),
            ],
        ),
        (
            "group_check_members",
            [
                sa.Column("kick_attempts", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("kick_join", sa.DateTime(timezone=True)),
            ],
        ),
        (
            "group_check_events",
            [
                sa.Column("grace_seconds", sa.Integer(), nullable=False, server_default="15"),
                sa.Column("kick_attempt", sa.Integer(), nullable=False, server_default="0"),
                sa.Column("matched_word", sa.String(100)),
                sa.Column(
                    "message_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
                ),
            ],
        ),
    ):
        for column in columns:
            op.add_column(table, column)


def downgrade():
    raise RuntimeError(
        "Retain word-filter settings and KICK history; use a reviewed forward rollback"
    )

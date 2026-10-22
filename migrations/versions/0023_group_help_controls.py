"""Independent word filters and durable per-member KICK rejoin escalation."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "group_moderation_settings",
        sa.Column(
            "local_auto_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )
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
    bind = op.get_bind()
    if (
        any(
            bind.execute(sa.text("SELECT count(*) FROM " + table)).scalar()
            for table in (
                "group_check_settings",
                "group_check_members",
                "group_check_events",
            )
        )
        or bind.execute(
            sa.text("SELECT count(*) FROM group_moderation_settings WHERE local_auto_enabled=true")
        ).scalar()
    ):
        raise RuntimeError(
            "Retain word-filter settings, local command access and KICK history; use a reviewed forward rollback"
        )
    for table, columns in {
        "group_check_events": ("message_deleted", "matched_word", "kick_attempt", "grace_seconds"),
        "group_check_members": ("kick_join", "kick_attempts"),
        "group_check_settings": ("words_delete", "words_action", "banned_words"),
        "group_moderation_settings": ("local_auto_enabled",),
    }.items():
        for column in columns:
            op.drop_column(table, column)

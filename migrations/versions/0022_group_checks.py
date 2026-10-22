"""Independent, initially disabled checks and durable notice-before-action outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_check_settings",
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("managed_groups.chat_id"), primary_key=True
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("actions", sa.JSON(), nullable=False),
        sa.Column("check_on_join", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("delete_messages", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notify_result", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("mute_seconds", sa.Integer(), nullable=False, server_default="86400"),
        sa.Column("spam_terms", sa.JSON(), nullable=False),
        sa.Column("language", sa.String(2), nullable=False, server_default="lt"),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version>0", name="ck_check_version"),
        sa.CheckConstraint("mute_seconds BETWEEN 300 AND 2592000", name="ck_check_duration"),
        sa.CheckConstraint("language IN ('lt','en','ru')", name="ck_check_language"),
    )
    op.create_table(
        "group_check_members",
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("managed_groups.chat_id"), primary_key=True
        ),
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("episodes", sa.JSON(), nullable=False),
    )
    op.create_table(
        "group_check_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("managed_groups.chat_id"), nullable=False
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("rule", sa.String(16), nullable=False),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_key", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("sanction_id", sa.Integer(), sa.ForeignKey("group_sanctions.id")),
        sa.Column("message_id", sa.BigInteger()),
        sa.Column("notice_message_id", sa.BigInteger()),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_check_events_chat_id", "group_check_events", ["chat_id"])
    op.create_index("ix_group_check_events_state", "group_check_events", ["state"])


def downgrade():
    if any(
        op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar()
        for table in ("group_check_settings", "group_check_members", "group_check_events")
    ):
        raise RuntimeError(
            "Retain Checks settings and audit history; use a reviewed forward rollback"
        )
    op.drop_table("group_check_events")
    op.drop_table("group_check_members")
    op.drop_table("group_check_settings")

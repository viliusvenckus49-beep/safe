"""Local commands and actor-bound confirmations; no changes to old migrations."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

OLD_STATUS = (
    "status IN ('PENDING','ACTIVE','REVOKING','UNCERTAIN','REVOKED','EXPIRED','CANCELLED','FAILED')"
)
NEW_STATUS = OLD_STATUS[:-1] + ",'RECORDED','COMPLETED')"


def upgrade():
    for name in ("warnings_enabled", "kicks_enabled", "allow_permanent_mutes"):
        op.add_column(
            "group_moderation_settings",
            sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    with op.batch_alter_table("group_sanctions") as batch:
        batch.drop_constraint("ck_group_sanction_kind", type_="check")
        batch.drop_constraint("ck_group_sanction_status", type_="check")
        batch.drop_constraint("ck_group_mute_expiry", type_="check")
        batch.create_check_constraint(
            "ck_group_sanction_kind", "kind IN ('BAN','MUTE','WARN','KICK')"
        )
        batch.create_check_constraint("ck_group_sanction_status", NEW_STATUS)
    op.create_table(
        "group_moderation_drafts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("managed_groups.chat_id"), nullable=False
        ),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("until_date", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sanction_id", sa.Integer(), sa.ForeignKey("group_sanctions.id")),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("panel_chat_id", sa.BigInteger()),
        sa.Column("panel_message_id", sa.BigInteger()),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("receipt_hash", sa.String(64)),
        sa.Column("receipt_checked_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_available_at", sa.DateTime(timezone=True)),
        sa.Column("receipt_done", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("actor_id > 0 AND telegram_id > 0", name="ck_group_draft_ids"),
        sa.CheckConstraint(
            "status IN ('PENDING','CONFIRMED','CANCELLED')", name="ck_group_draft_status"
        ),
        sa.CheckConstraint(
            "kind IN ('BAN','MUTE','WARN','KICK','UNBAN','UNMUTE')", name="ck_group_draft_kind"
        ),
    )
    op.create_index("ix_group_draft_expiry", "group_moderation_drafts", ["status", "expires_at"])


def downgrade():
    # Never silently discard stage-2 history or convert a permanent mute into a ban.
    incompatible = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM group_sanctions WHERE kind IN ('WARN','KICK') "
                "OR status IN ('RECORDED','COMPLETED') OR (kind='MUTE' AND until_date IS NULL)"
            )
        )
        .scalar()
    )
    drafts = op.get_bind().execute(sa.text("SELECT count(*) FROM group_moderation_drafts")).scalar()
    new_settings = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM group_moderation_settings WHERE warnings_enabled = true "
                "OR kicks_enabled = true OR allow_permanent_mutes = true"
            )
        )
        .scalar()
    )
    if incompatible or drafts or new_settings:
        raise RuntimeError("Stage-2 data exists; retain schema and use a reviewed forward rollback")
    op.drop_index("ix_group_draft_expiry", table_name="group_moderation_drafts")
    op.drop_table("group_moderation_drafts")
    with op.batch_alter_table("group_sanctions") as batch:
        batch.drop_constraint("ck_group_sanction_kind", type_="check")
        batch.drop_constraint("ck_group_sanction_status", type_="check")
        batch.create_check_constraint("ck_group_sanction_kind", "kind IN ('BAN','MUTE')")
        batch.create_check_constraint("ck_group_sanction_status", OLD_STATUS)
        batch.create_check_constraint(
            "ck_group_mute_expiry", "kind != 'MUTE' OR until_date IS NOT NULL"
        )
    for name in ("warnings_enabled", "kicks_enabled", "allow_permanent_mutes"):
        op.drop_column("group_moderation_settings", name)

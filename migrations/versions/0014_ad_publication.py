"""Persistent AUTO ADS schedules and fenced composite publication attempts."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_publication_gate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("until_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_ad_gate_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "ad_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="DRAFT", nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("start_time", sa.String(length=12), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("authorized_by", sa.BigInteger(), nullable=False),
        sa.Column("activated_revision", sa.Integer(), nullable=True),
        sa.Column("activation_base", sa.Integer(), nullable=True),
        sa.Column("last_request", sa.String(length=128), nullable=True),
        sa.Column("last_payload", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("alert_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status != 'ACTIVE' OR (chat_id IS NOT NULL AND interval_seconds IS NOT NULL AND start_at IS NOT NULL AND next_run_at IS NOT NULL)",
            name="ck_ad_schedule_active",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','STOPPED','EXPIRED')",
            name="ck_ad_schedule_status",
        ),
        sa.CheckConstraint("chat_id IS NULL OR chat_id < 0", name="ck_ad_schedule_chat"),
        sa.CheckConstraint(
            "end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="ck_ad_schedule_dates"
        ),
        sa.CheckConstraint(
            "interval_seconds IS NULL OR interval_seconds IN (1800,3600,7200,10800,14400,21600,28800,43200,86400)",
            name="ck_ad_schedule_interval",
        ),
        sa.CheckConstraint("version > 0 AND authorized_by > 0", name="ck_ad_schedule_ids"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaigns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["managed_groups.chat_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("campaign_id", "chat_id", name="uq_ad_schedule_group"),
    )
    op.create_index(
        op.f("ix_ad_schedules_next_run_at"), "ad_schedules", ["next_run_at"], unique=False
    )
    op.create_table(
        "ad_publication_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=False),
        sa.Column("content_version_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=True),
        sa.Column("fence", sa.Integer(), server_default="0", nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("notified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','SENDING','SENT','FAILED','DEFERRED','UNCERTAIN','CANCELLED')",
            name="ck_ad_attempt_status",
        ),
        sa.CheckConstraint(
            "status NOT IN ('CLAIMED','SENDING') OR (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_ad_attempt_lease",
        ),
        sa.CheckConstraint(
            "fence >= 0 AND schedule_version > 0 AND chat_id < 0", name="ck_ad_attempt_ids"
        ),
        sa.ForeignKeyConstraint(
            ["content_version_id"],
            ["ad_content_versions.id"],
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["ad_schedules.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("schedule_id", "due_at", name="uq_ad_publication_slot"),
    )
    op.create_index(
        "ix_ad_attempt_work",
        "ad_publication_attempts",
        ["status", "retry_at", "lease_until"],
        unique=False,
    )
    op.create_table(
        "ad_publication_parts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("message_ids", sa.JSON(), nullable=False),
        sa.Column("tries", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "kind IN ('TEXT','PHOTO','VIDEO','ALBUM','BUTTONS')", name="ck_ad_part_kind"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SENDING','SENT','FAILED','DEFERRED','UNCERTAIN','CANCELLED')",
            name="ck_ad_part_status",
        ),
        sa.CheckConstraint("position >= 0 AND tries >= 0", name="ck_ad_part_numbers"),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["ad_publication_attempts.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "position", name="uq_ad_publication_part"),
    )


def downgrade():
    for table in (
        "ad_schedules",
        "ad_publication_attempts",
        "ad_publication_parts",
        "ad_publication_gate",
    ):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar():
            raise RuntimeError("Publication history exists; use a reviewed forward rollback")
    op.drop_table("ad_publication_parts")
    op.drop_index("ix_ad_attempt_work", table_name="ad_publication_attempts")
    op.drop_table("ad_publication_attempts")
    op.drop_index(op.f("ix_ad_schedules_next_run_at"), table_name="ad_schedules")
    op.drop_table("ad_schedules")
    op.drop_table("ad_publication_gate")

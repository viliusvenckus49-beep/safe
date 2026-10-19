"""global ads assignments operations and rate budgets"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_assignment_policies",
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=10), server_default="SINGLE", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("interval_seconds", sa.Integer(), server_default="3600", nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auto_activate", sa.Boolean(), server_default=sa.text("(false)"), nullable=False),
        sa.Column("authorized_by", sa.BigInteger(), nullable=False),
        sa.Column("scan_after", sa.BigInteger(), nullable=True),
        sa.Column("reconcile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "mode IN ('SINGLE','SELECTED','EXISTING','FUTURE')", name="ck_ad_assignment_mode"
        ),
        sa.CheckConstraint(
            "end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="ck_ad_assignment_dates"
        ),
        sa.CheckConstraint(
            "interval_seconds IN (1800,3600,7200,10800,14400,21600,28800,43200,86400)",
            name="ck_ad_assignment_interval",
        ),
        sa.CheckConstraint("version > 0 AND authorized_by > 0", name="ck_ad_assignment_ids"),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaigns.id"],
        ),
        sa.PrimaryKeyConstraint("campaign_id"),
    )
    op.create_index(
        "ix_ad_assignment_reconcile",
        "ad_assignment_policies",
        ["mode", "reconcile_at", "campaign_id"],
        unique=False,
    )
    op.create_table(
        "ad_chat_publication_gates",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("chat_id < 0", name="ck_ad_chat_gate_id"),
        sa.ForeignKeyConstraint(
            ["chat_id"],
            ["managed_groups.chat_id"],
        ),
        sa.PrimaryKeyConstraint("chat_id"),
    )
    op.create_table(
        "ad_global_operations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("content_revision", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('ASSIGN','START','PAUSE','STOP','REMOVE','DEFAULTS','INHERIT','CHANGE','FUTURE_ON','FUTURE_OFF')",
            name="ck_ad_global_action",
        ),
        sa.CheckConstraint(
            "status IN ('SELECTING','READY','RUNNING','DONE','CANCELLED')",
            name="ck_ad_global_status",
        ),
        sa.CheckConstraint(
            "actor_id > 0 AND version > 0 AND policy_version > 0", name="ck_ad_global_ids"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaigns.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_key"),
    )
    op.create_index(
        "ix_ad_global_resume", "ad_global_operations", ["status", "created_at"], unique=False
    )
    op.create_table(
        "ad_global_operation_items",
        sa.Column("operation_id", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("schedule_id", sa.Integer(), nullable=True),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), server_default="ASSIGN", nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("token", sa.String(length=32), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.CheckConstraint(
            "status != 'CLAIMED' OR (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_ad_global_item_lease",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','CLAIMED','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_ad_global_item_status",
        ),
        sa.CheckConstraint("chat_id < 0", name="ck_ad_global_item_chat"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["ad_global_operations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            ["ad_schedules.id"],
        ),
        sa.PrimaryKeyConstraint("operation_id", "chat_id"),
    )
    op.create_index(
        "ix_ad_global_item_work",
        "ad_global_operation_items",
        ["operation_id", "status", "lease_until"],
        unique=False,
    )
    op.create_index(
        "ix_ad_attempt_schedule_status",
        "ad_publication_attempts",
        ["schedule_id", "status", "id"],
        unique=False,
    )
    op.add_column(
        "ad_publication_gate", sa.Column("next_send_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "ad_schedules",
        sa.Column("excluded", sa.Boolean(), server_default=sa.text("(false)"), nullable=False),
    )
    op.add_column(
        "ad_schedules",
        sa.Column(
            "override_fields",
            sa.JSON(),
            server_default=sa.text('\'["interval_seconds","start_at","end_at"]\''),
            nullable=False,
        ),
    )
    op.add_column(
        "ad_schedules", sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_ad_schedule_due", "ad_schedules", ["status", "next_run_at", "id"], unique=False
    )
    # Derived rows only: do not change legacy schedules, content or publication history.
    op.execute(
        sa.text(
            "INSERT INTO ad_assignment_policies(campaign_id,mode,version,interval_seconds,auto_activate,authorized_by,updated_at) SELECT id,'SINGLE',1,3600,false,created_by,updated_at FROM ad_campaigns"
        )
    )


def downgrade():
    for table in (
        "ad_assignment_policies",
        "ad_global_operations",
        "ad_global_operation_items",
        "ad_chat_publication_gates",
    ):
        if op.get_bind().execute(sa.text("SELECT count(*) FROM " + table)).scalar():
            raise RuntimeError("GLOBAL ADS data exists; use a reviewed forward rollback")

    op.drop_index("ix_ad_schedule_due", table_name="ad_schedules")
    op.drop_column("ad_schedules", "last_dispatched_at")
    op.drop_column("ad_schedules", "override_fields")
    op.drop_column("ad_schedules", "excluded")
    op.drop_column("ad_publication_gate", "next_send_at")
    op.drop_index("ix_ad_attempt_schedule_status", table_name="ad_publication_attempts")
    op.drop_index("ix_ad_global_item_work", table_name="ad_global_operation_items")
    op.drop_table("ad_global_operation_items")
    op.drop_index("ix_ad_global_resume", table_name="ad_global_operations")
    op.drop_table("ad_global_operations")
    op.drop_table("ad_chat_publication_gates")
    op.drop_index("ix_ad_assignment_reconcile", table_name="ad_assignment_policies")
    op.drop_table("ad_assignment_policies")

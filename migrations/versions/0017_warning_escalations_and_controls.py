"""Durable warning escalation and actor-bound warning reset confirmations."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "group_warning_escalations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "chat_id", sa.BigInteger(), sa.ForeignKey("managed_groups.chat_id"), nullable=False
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "trigger_id",
            sa.Integer(),
            sa.ForeignKey("group_sanctions.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("sanction_id", sa.Integer(), sa.ForeignKey("group_sanctions.id"), unique=True),
        sa.Column("draft_id", sa.String(32), sa.ForeignKey("group_moderation_drafts.id")),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("result", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("telegram_id > 0", name="ck_warning_escalation_user"),
        sa.CheckConstraint(
            "state IN ('PENDING','SUCCEEDED','FAILED','UNCERTAIN','CANCELLED')",
            name="ck_warning_escalation_state",
        ),
    )
    op.create_index(
        "ix_warning_escalation_target",
        "group_warning_escalations",
        ["chat_id", "telegram_id", "state"],
    )
    op.create_table(
        "group_warning_resets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("origin_id", sa.Integer(), sa.ForeignKey("group_sanctions.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("max_warning_id", sa.Integer(), nullable=False),
        sa.Column("expected_count", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "actor_id > 0 AND expected_count > 0", name="ck_warning_reset_actor_count"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','CONFIRMED','CANCELLED')", name="ck_warning_reset_state"
        ),
    )


def downgrade():
    bind = op.get_bind()
    if any(
        bind.execute(sa.text("SELECT count(*) FROM " + table)).scalar()
        for table in ("group_warning_escalations", "group_warning_resets")
    ):
        raise RuntimeError(
            "Warning control history exists; retain schema and use a reviewed forward rollback"
        )
    op.drop_table("group_warning_resets")
    op.drop_index("ix_warning_escalation_target", table_name="group_warning_escalations")
    op.drop_table("group_warning_escalations")

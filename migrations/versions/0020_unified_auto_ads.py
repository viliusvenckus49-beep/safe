"""Unified AUTO ADS invitations, explicit audience snapshots and additional media."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_client_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("ad_campaigns.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("expected_grant", sa.String(48), nullable=False),
        sa.Column("expected_customer_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_by", sa.BigInteger(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grant_id", sa.Integer(), sa.ForeignKey("ad_customer_grants.id"), nullable=True),
        sa.CheckConstraint("status IN ('ACTIVE','USED','REVOKED')", name="ck_ad_invite_status"),
        sa.CheckConstraint("length(token_hash) = 64 AND created_by > 0", name="ck_ad_invite_hash"),
        sa.CheckConstraint("expires_at > created_at", name="ck_ad_invite_expiry"),
        sa.CheckConstraint(
            "expected_customer_id IS NULL OR expected_customer_id > 0", name="ck_ad_invite_customer"
        ),
        sa.CheckConstraint(
            "(status = 'USED' AND consumed_by IS NOT NULL AND consumed_by > 0 AND consumed_at IS NOT NULL AND grant_id IS NOT NULL) OR (status <> 'USED' AND consumed_by IS NULL AND consumed_at IS NULL AND grant_id IS NULL)",
            name="ck_ad_invite_consumption",
        ),
    )
    op.create_index(
        "uq_ad_active_invite",
        "ad_client_invitations",
        ["campaign_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    with op.batch_alter_table("ad_publication_parts") as batch:
        batch.alter_column("kind", existing_type=sa.String(8), type_=sa.String(9))
        batch.drop_constraint("ck_ad_part_kind", type_="check")
        batch.create_check_constraint(
            "ck_ad_part_kind",
            "kind IN ('TEXT','PHOTO','VIDEO','ANIMATION','DOCUMENT','ALBUM','BUTTONS')",
        )
    # Only future auto-enrolment changes; retain all existing per-group schedules.
    policies = sa.table(
        "ad_assignment_policies",
        sa.column("campaign_id", sa.Integer()),
        sa.column("authorized_by", sa.BigInteger()),
        sa.column("mode", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("auto_activate", sa.Boolean()),
        sa.column("reconcile_at", sa.DateTime(timezone=True)),
    )
    audit = sa.table(
        "audit_events",
        sa.column("actor_id", sa.BigInteger()),
        sa.column("action", sa.String()),
        sa.column("details", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    from datetime import UTC, datetime

    for row in (
        op.get_bind().execute(sa.select(policies).where(policies.c.mode == "FUTURE")).mappings()
    ):
        op.get_bind().execute(
            audit.insert().values(
                actor_id=row["authorized_by"],
                action="ads_audience_snapshot_migrated",
                details={
                    "campaign_id": row["campaign_id"],
                    "previous_mode": "FUTURE",
                    "previous_version": row["version"],
                },
                created_at=datetime.now(UTC),
            )
        )
    op.get_bind().execute(
        policies.update()
        .where(policies.c.mode == "FUTURE")
        .values(
            mode="EXISTING", auto_activate=False, reconcile_at=None, version=policies.c.version + 1
        )
    )


def downgrade():
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM ad_client_invitations")).scalar():
        raise RuntimeError("Retain AUTO ADS invitation history; use a forward rollback")
    if connection.execute(
        sa.text("SELECT count(*) FROM ad_publication_parts WHERE kind IN ('ANIMATION','DOCUMENT')")
    ).scalar():
        raise RuntimeError("Retain AUTO ADS media history; use a forward rollback")
    with op.batch_alter_table("ad_publication_parts") as batch:
        batch.drop_constraint("ck_ad_part_kind", type_="check")
        batch.create_check_constraint(
            "ck_ad_part_kind", "kind IN ('TEXT','PHOTO','VIDEO','ALBUM','BUTTONS')"
        )
    op.drop_index("uq_ad_active_invite", table_name="ad_client_invitations")
    op.drop_table("ad_client_invitations")
    # Never silently restore FUTURE audience expansion on downgrade.

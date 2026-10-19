"""ad customer capabilities and hashed links"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_customer_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("editing_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by > 0)",
            name="ck_ad_grant_status",
        ),
        sa.CheckConstraint(
            "customer_id > 0 AND assigned_by > 0 AND version > 0", name="ck_ad_grant_ids"
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaigns.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "campaign_id", "customer_id", name="uq_ad_grant_identity"),
    )
    op.create_index(
        "ix_ad_customer_campaigns",
        "ad_customer_grants",
        ["customer_id", "status", "campaign_id"],
        unique=False,
    )
    op.create_index(
        "uq_ad_active_customer",
        "ad_customer_grants",
        ["campaign_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index("ix_ad_grant_campaign", "ad_customer_grants", ["campaign_id", "id"])
    op.create_table(
        "ad_customer_drafts",
        sa.Column("draft_id", sa.String(length=32), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["ad_campaigns.id"],
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["ad_content_drafts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "campaign_id", "customer_id"],
            [
                "ad_customer_grants.id",
                "ad_customer_grants.campaign_id",
                "ad_customer_grants.customer_id",
            ],
            name="fk_ad_draft_customer",
        ),
        sa.PrimaryKeyConstraint("draft_id"),
    )
    op.create_table(
        "ad_customer_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("grant_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_by", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by > 0)",
            name="ck_ad_link_status",
        ),
        sa.CheckConstraint("length(token_hash) = 64 AND created_by > 0", name="ck_ad_link_hash"),
        sa.ForeignKeyConstraint(
            ["grant_id"],
            ["ad_customer_grants.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "uq_ad_active_link",
        "ad_customer_links",
        ["grant_id"],
        unique=True,
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )


def downgrade():
    for name in ("ad_customer_grants", "ad_customer_drafts", "ad_customer_links"):
        if op.get_bind().execute(sa.text(f'SELECT count(*) FROM "{name}"')).scalar():
            raise RuntimeError("Customer ADS data exists; downgrade would lose history")
    op.drop_index(
        "uq_ad_active_link",
        table_name="ad_customer_links",
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_table("ad_customer_links")
    op.drop_table("ad_customer_drafts")
    op.drop_index(
        "uq_ad_active_customer",
        table_name="ad_customer_grants",
        sqlite_where=sa.text("status = 'ACTIVE'"),
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index("ix_ad_customer_campaigns", table_name="ad_customer_grants")
    op.drop_index("ix_ad_grant_campaign", table_name="ad_customer_grants")
    op.drop_table("ad_customer_grants")

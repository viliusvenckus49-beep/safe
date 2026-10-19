"""AUTO ADS stage 3.1: immutable content versions, private durable edit drafts."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ad_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("internal_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("client_telegram_id", sa.BigInteger()),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('DRAFT','ARCHIVED')", name="ck_ad_campaign_status"),
        sa.CheckConstraint(
            "version > 0 AND created_by > 0 AND updated_by > 0", name="ck_ad_campaign_ids"
        ),
        sa.CheckConstraint(
            "client_telegram_id IS NULL OR client_telegram_id > 0", name="ck_ad_client_id"
        ),
    )
    op.create_table(
        "ad_content_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("ad_campaigns.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("campaign_id", "revision", name="uq_ad_content_revision"),
        sa.CheckConstraint("revision > 0 AND actor_id > 0", name="ck_ad_content_ids"),
    )
    op.create_table(
        "ad_content_drafts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("ad_campaigns.id"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("field", sa.String(12), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("previewed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("saved_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('COLLECTING','READY','SAVED','CANCELLED')", name="ck_ad_draft_status"
        ),
        sa.CheckConstraint(
            "field IN ('TEXT','MEDIA','BUTTONS','RESTORE')", name="ck_ad_draft_field"
        ),
        sa.CheckConstraint("actor_id > 0 AND base_version > 0", name="ck_ad_draft_ids"),
    )
    op.create_index("ix_ad_draft_owner", "ad_content_drafts", ["actor_id", "status", "expires_at"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM ad_campaigns")).scalar():
        raise RuntimeError("Ad data exists; retain schema and prepare a reviewed forward rollback")
    op.drop_index("ix_ad_draft_owner", table_name="ad_content_drafts")
    op.drop_table("ad_content_drafts")
    op.drop_table("ad_content_versions")
    op.drop_table("ad_campaigns")

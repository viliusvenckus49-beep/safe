"""Persist per-group warning policy; retain the existing three-warning MUTE default."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("group_moderation_settings") as batch:
        batch.add_column(
            sa.Column("warning_limit", sa.Integer(), nullable=False, server_default="3")
        )
        batch.add_column(
            sa.Column("warning_action", sa.String(4), nullable=False, server_default="MUTE")
        )
        batch.add_column(
            sa.Column("warning_mute_seconds", sa.Integer(), nullable=False, server_default="86400")
        )
        batch.add_column(
            sa.Column("warning_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.create_check_constraint("ck_group_warning_limit", "warning_limit BETWEEN 2 AND 6")
        batch.create_check_constraint(
            "ck_group_warning_action", "warning_action IN ('OFF','KICK','MUTE','BAN')"
        )
        batch.create_check_constraint(
            "ck_group_warning_mute", "warning_mute_seconds BETWEEN 60 AND 31536000"
        )
        batch.create_check_constraint("ck_group_warning_version", "warning_version > 0")
    with op.batch_alter_table("group_warning_escalations") as batch:
        batch.add_column(
            sa.Column("warning_limit", sa.Integer(), nullable=False, server_default="3")
        )
        batch.create_check_constraint(
            "ck_escalation_warning_limit", "warning_limit BETWEEN 2 AND 6"
        )


def downgrade():
    # Only the untouched legacy 3-WARN/24h MUTE policy has an equivalent in 0020.
    # A changed-and-restored policy still has history (warning_version > 1).
    connection = op.get_bind()
    changed = connection.execute(
        sa.text(
            "SELECT count(*) FROM group_moderation_settings WHERE warning_limit<>3 "
            "OR warning_action<>'MUTE' OR warning_mute_seconds<>86400 OR warning_version<>1"
        )
    ).scalar()
    snapshots = connection.execute(
        sa.text("SELECT count(*) FROM group_warning_escalations WHERE warning_limit<>3")
    ).scalar()
    if changed or snapshots:
        raise RuntimeError("Retain warning settings and history; use a reviewed forward rollback")
    with op.batch_alter_table("group_warning_escalations") as batch:
        batch.drop_constraint("ck_escalation_warning_limit", type_="check")
        batch.drop_column("warning_limit")
    with op.batch_alter_table("group_moderation_settings") as batch:
        for name in (
            "ck_group_warning_limit",
            "ck_group_warning_action",
            "ck_group_warning_mute",
            "ck_group_warning_version",
        ):
            batch.drop_constraint(name, type_="check")
        for name in ("warning_limit", "warning_action", "warning_mute_seconds", "warning_version"):
            batch.drop_column(name)

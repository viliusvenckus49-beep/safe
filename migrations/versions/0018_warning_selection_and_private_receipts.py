"""Select an individual warning and distinguish private from public receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("group_warning_resets") as batch:
        batch.add_column(sa.Column("selected_warning_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_warning_reset_selected", "group_sanctions", ["selected_warning_id"], ["id"]
        )
    op.add_column(
        "group_moderation_drafts", sa.Column("receipt_visibility", sa.String(8), nullable=True)
    )
    op.add_column(
        "group_moderation_drafts", sa.Column("original_command", sa.String(64), nullable=True)
    )


def downgrade():
    bind = op.get_bind()
    populated = (
        bind.execute(
            sa.text(
                "SELECT count(*) FROM group_warning_resets WHERE selected_warning_id IS NOT NULL"
            )
        ).scalar()
        or bind.execute(
            sa.text(
                "SELECT count(*) FROM group_moderation_drafts WHERE receipt_visibility IS NOT NULL OR original_command IS NOT NULL"
            )
        ).scalar()
    )
    if populated:
        raise RuntimeError(
            "Retain warning selections and receipt visibility; use a forward rollback"
        )
    # Only unused nullable additions may be removed. Older downgrade guards
    # remain responsible for retaining all pre-existing warnings and history.
    with op.batch_alter_table("group_warning_resets") as batch:
        batch.drop_constraint("fk_warning_reset_selected", type_="foreignkey")
        batch.drop_column("selected_warning_id")
    op.drop_column("group_moderation_drafts", "original_command")
    op.drop_column("group_moderation_drafts", "receipt_visibility")

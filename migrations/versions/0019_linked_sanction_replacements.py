"""Retain an owned MUTE while its linked BAN/KICK is being confirmed."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

LIVE = "status IN ('PENDING','ACTIVE','REVOKING','UNCERTAIN')"


def upgrade():
    with op.batch_alter_table("group_sanctions") as batch:
        batch.add_column(sa.Column("replaces_sanction_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_sanction_replaces", "group_sanctions", ["replaces_sanction_id"], ["id"]
        )
    op.drop_index("uq_group_live_sanction", table_name="group_sanctions")
    op.create_index(
        "uq_group_live_sanction",
        "group_sanctions",
        ["chat_id", "telegram_id", "kind"],
        unique=True,
        sqlite_where=sa.text(LIVE),
        postgresql_where=sa.text(LIVE),
    )


def downgrade():
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM group_sanctions WHERE replaces_sanction_id IS NOT NULL")
        )
        .scalar()
    ):
        raise RuntimeError("Retain sanction replacement history; use a forward rollback")
    # A cross-kind duplicate must never be discarded just to restore an index.
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM (SELECT chat_id,telegram_id FROM group_sanctions WHERE "
                + LIVE
                + " GROUP BY chat_id,telegram_id HAVING count(*)>1) duplicate_live"
            )
        )
        .scalar()
    ):
        raise RuntimeError("Retain active restrictions; use a forward rollback")
    op.drop_index("uq_group_live_sanction", table_name="group_sanctions")
    op.create_index(
        "uq_group_live_sanction",
        "group_sanctions",
        ["chat_id", "telegram_id"],
        unique=True,
        sqlite_where=sa.text(LIVE),
        postgresql_where=sa.text(LIVE),
    )
    with op.batch_alter_table("group_sanctions") as batch:
        batch.drop_constraint("fk_sanction_replaces", type_="foreignkey")
        batch.drop_column("replaces_sanction_id")

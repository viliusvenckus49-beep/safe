"""Real PostgreSQL 0017 -> 0018 history retention and Alembic drift checks."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

spec = importlib.util.spec_from_file_location(
    "professional_migration",
    Path(__file__).resolve().parents[1]
    / "migrations/versions/0018_warning_selection_and_private_receipts.py",
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.asyncio
async def test_postgres_0018_preserves_prior_warning_confirmations_and_adds_nullable_fields(
    postgres_contract,
):
    sessions, _ = postgres_contract
    async with sessions.kw["bind"].begin() as connection:
        await connection.execute(
            text("ALTER TABLE group_warning_resets DROP COLUMN selected_warning_id CASCADE")
        )
        await connection.execute(
            text("ALTER TABLE group_moderation_drafts DROP COLUMN receipt_visibility")
        )
        await connection.execute(
            text("ALTER TABLE group_moderation_drafts DROP COLUMN original_command")
        )
        for statement in SEED:
            await connection.execute(text(statement))
        await connection.execute(
            text(
                "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,41,'WARN',900,'Preserve','RECORDED',false,1,'migration-preserve',now(),now())"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO group_warning_resets(id,origin_id,actor_id,max_warning_id,expected_count,state,expires_at,created_at) VALUES('retained',1,900,1,1,'CONFIRMED',now(),now())"
            )
        )
        before = (await connection.execute(text("SELECT * FROM group_sanctions"))).all()

        def upgrade(c):
            with Operations.context(MigrationContext.configure(c)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        assert (await connection.execute(text("SELECT * FROM group_sanctions"))).all() == before
        assert (
            await connection.execute(
                text("SELECT id,state,selected_warning_id FROM group_warning_resets")
            )
        ).one() == ("retained", "CONFIRMED", None)
        await connection.execute(
            text("UPDATE group_warning_resets SET selected_warning_id=1 WHERE id='retained'")
        )
        assert (
            await connection.execute(text("SELECT selected_warning_id FROM group_warning_resets"))
        ).scalar() == 1
        with pytest.raises(RuntimeError, match="forward rollback"):

            def downgrade(c):
                with Operations.context(MigrationContext.configure(c)):
                    migration.downgrade()

            await connection.run_sync(downgrade)

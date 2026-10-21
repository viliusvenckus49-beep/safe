"""Real PostgreSQL: replacement migration retains rows and protects history."""

import importlib.util
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

from app.models import Base

spec = importlib.util.spec_from_file_location(
    "replacement_migration",
    Path(__file__).resolve().parents[1]
    / "migrations/versions/0019_linked_sanction_replacements.py",
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


@pytest.mark.asyncio
async def test_postgres_replacement_upgrade_retains_rows_and_downgrade_refuses_used_links(
    postgres_contract,
):
    factory, _ = postgres_contract
    async with factory.kw["bind"].begin() as connection:
        for statement in SEED:
            await connection.execute(text(statement))

        def run(c, action):
            with Operations.context(MigrationContext.configure(c)):
                action()

        await connection.run_sync(lambda c: run(c, migration.downgrade))
        await connection.execute(
            text(
                "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,51,'MUTE',900,'Preserve mute','ACTIVE',true,1,'migration-mute',now(),now())"
            )
        )
        before = (
            await connection.execute(text("SELECT id,status,reason,owned FROM group_sanctions"))
        ).all()
        await connection.run_sync(lambda c: run(c, migration.upgrade))
        assert (
            await connection.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c), Base.metadata)
            )
            == []
        )
        assert (
            await connection.execute(text("SELECT id,status,reason,owned FROM group_sanctions"))
        ).all() == before
        assert (
            await connection.execute(text("SELECT replaces_sanction_id FROM group_sanctions"))
        ).scalar() is None
        # The live MUTE remains during a linked BAN. A second live BAN is still
        # excluded by the unique index; ordinary cross-kind writes are guarded
        # by group-serialized service authorization and transition validation.
        await connection.execute(
            text(
                "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,replaces_sanction_id,created_at,updated_at) VALUES(2,-100,51,'BAN',900,'Linked','PENDING',false,1,'migration-ban',1,now(),now())"
            )
        )
        with pytest.raises(RuntimeError, match="forward rollback"):
            await connection.run_sync(lambda c: run(c, migration.downgrade))
        assert (
            await connection.execute(text("SELECT count(*) FROM group_sanctions"))
        ).scalar() == 2

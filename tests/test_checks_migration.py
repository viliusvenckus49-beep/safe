"""Actual additive 0022 DDL, full legacy-row preservation and guarded rollback."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from test_auto_ads_migration import migrate
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

from app.models import Base

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "checks_migration", ROOT / "migrations/versions/0022_group_checks.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_sqlite_0022_preserves_all_original_rows_and_durable_deadline(tmp_path):
    path = tmp_path / "checks.db"
    migrate(path, "upgrade", "0021")
    with sqlite3.connect(path) as db:
        for sql in SEED:
            db.execute(sql)
        db.execute(
            "INSERT INTO group_moderation_settings(chat_id,updated_at,warning_limit,warning_action) VALUES(-100,CURRENT_TIMESTAMP,5,'BAN')"
        )
        tables = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        before = {name: db.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables}
    migrate(path, "upgrade", "head")
    migrate(path, "check")
    with sqlite3.connect(path) as db:
        assert all(
            db.execute(f'SELECT * FROM "{name}"').fetchall() == before[name] for name in tables
        )
        assert all(
            db.execute("SELECT count(*) FROM " + name).fetchone() == (0,)
            for name in ("group_check_settings", "group_check_members", "group_check_events")
        )
        db.execute(
            "INSERT INTO group_check_settings(chat_id,actions,spam_terms,updated_by,updated_at) VALUES(-100,'{\"username\":\"KICK\"}','[]',900,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO group_check_events(chat_id,telegram_id,display_name,rule,action,version,request_key,state,notice_message_id,available_at,created_at,updated_at) VALUES(-100,8896102663,'Actual name','username','KICK',1,'persist-grace','WAITING',900,'2030-01-01T12:00:15+00:00',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT state,notice_message_id,available_at FROM group_check_events"
        ).fetchone() == ("WAITING", 900, "2030-01-01T12:00:15+00:00")
    migrate(path, "downgrade", "0021", success=False)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT display_name FROM group_check_events").fetchone() == (
            "Actual name",
        )


@pytest.mark.asyncio
async def test_real_postgres_0022_tables_constraints_and_models_match(postgres_contract):
    factory, _ = postgres_contract
    async with factory.kw["bind"].begin() as connection:
        for name in ("group_check_events", "group_check_members", "group_check_settings"):
            await connection.execute(text("DROP TABLE " + name))

        def run(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()
                next_spec = importlib.util.spec_from_file_location(
                    "controls_migration", ROOT / "migrations/versions/0023_group_help_controls.py"
                )
                next_migration = importlib.util.module_from_spec(next_spec)
                next_spec.loader.exec_module(next_migration)
                next_migration.upgrade()
            diffs = compare_metadata(
                MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata
            )
            assert not diffs, diffs

        await connection.run_sync(run)

"""0023 preserves every original column, including existing Checks history."""

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
    "controls_migration", ROOT / "migrations/versions/0023_group_help_controls.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_sqlite_0023_preserves_all_old_checks_columns_and_defaults(tmp_path):
    path = tmp_path / "controls.db"
    migrate(path, "upgrade", "0022")
    with sqlite3.connect(path) as db:
        for sql in SEED:
            db.execute(sql)
        db.execute(
            "INSERT INTO group_check_settings(chat_id,actions,spam_terms,updated_by,updated_at) VALUES(-100,'{\"username\":\"KICK\"}','[]',900,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO group_check_members(chat_id,telegram_id,joined_at,checked_at,episodes) VALUES(-100,8896102663,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,'{}')"
        )
        db.execute(
            "INSERT INTO group_check_events(chat_id,telegram_id,display_name,rule,action,version,request_key,state,available_at,created_at,updated_at) VALUES(-100,8896102663,'Real','username','KICK',1,'legacy','DONE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        columns = {
            row[0]: [r[1] for r in db.execute('PRAGMA table_info("' + row[0] + '")')]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        }
        before = {
            table: db.execute('SELECT * FROM "' + table + '"').fetchall() for table in columns
        }
    migrate(path, "upgrade", "head")
    migrate(path, "check")
    with sqlite3.connect(path) as db:
        for table, names in columns.items():
            assert (
                db.execute(
                    "SELECT " + ",".join('"' + n + '"' for n in names) + ' FROM "' + table + '"'
                ).fetchall()
                == before[table]
            )
        assert db.execute(
            "SELECT banned_words,words_action,words_delete FROM group_check_settings"
        ).fetchone() == ("[]", "OFF", 1)
        assert db.execute("SELECT kick_attempts FROM group_check_members").fetchone() == (0,)
        assert db.execute(
            "SELECT grace_seconds,kick_attempt FROM group_check_events"
        ).fetchone() == (15, 0)
    migrate(path, "downgrade", "0022", success=False)


@pytest.mark.asyncio
async def test_real_postgres_additive_0023_matches_models(postgres_contract):
    factory, _ = postgres_contract
    async with factory.kw["bind"].begin() as connection:
        for table, columns in {
            "group_check_settings": ("banned_words", "words_action", "words_delete"),
            "group_check_members": ("kick_attempts", "kick_join"),
            "group_check_events": (
                "grace_seconds",
                "kick_attempt",
                "matched_word",
                "message_deleted",
            ),
        }.items():
            for column in columns:
                await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))

        def run(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()
            assert not compare_metadata(
                MigrationContext.configure(sync, opts={"compare_type": True}), Base.metadata
            )

        await connection.run_sync(run)

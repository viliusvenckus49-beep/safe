"""0020 -> 0021 preserves old columns, warnings, ads and per-group default policy."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from test_auto_ads_migration import migrate
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "warning_settings_migration", ROOT / "migrations/versions/0021_group_warning_settings.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_sqlite_all_legacy_columns_and_warning_rows_survive_upgrade(tmp_path):
    path = tmp_path / "warning-settings.db"
    migrate(path, "upgrade", "0020")
    with sqlite3.connect(path) as db:
        for sql in SEED:
            db.execute(sql)
        db.execute(
            "INSERT INTO group_moderation_settings(chat_id,updated_at) VALUES(-100,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,41,'WARN',900,'Keep reason','RECORDED',false,1,'warning-retention',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO group_warning_escalations(id,chat_id,telegram_id,trigger_id,state,created_at,updated_at) VALUES(1,-100,41,1,'FAILED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        names = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        columns = {
            name: [r[1] for r in db.execute(f'PRAGMA table_info("{name}")')] for name in names
        }
        before = {
            name: db.execute(f'SELECT {",".join(columns[name])} FROM "{name}"').fetchall()
            for name in names
        }
    migrate(path, "upgrade", "head")
    migrate(path, "check")
    with sqlite3.connect(path) as db:
        for name in names:
            assert (
                db.execute(f'SELECT {",".join(columns[name])} FROM "{name}"').fetchall()
                == before[name]
            )
        assert db.execute(
            "SELECT warning_limit,warning_action,warning_mute_seconds,warning_version FROM group_moderation_settings"
        ).fetchone() == (3, "MUTE", 86400, 1)
        assert db.execute("SELECT warning_limit FROM group_warning_escalations").fetchone() == (3,)
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        db.execute(
            "UPDATE group_moderation_settings SET warning_limit=6,warning_action='BAN',warning_mute_seconds=3600"
        )
    # An independent connection after migration/changes represents a new process.
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT warning_limit,warning_action,warning_mute_seconds FROM group_moderation_settings"
        ).fetchone() == (6, "BAN", 3600)
    migrate(path, "downgrade", "0020", success=False)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT reason,status FROM group_sanctions").fetchone() == (
            "Keep reason",
            "RECORDED",
        )


@pytest.mark.asyncio
async def test_postgres_actual_0021_ddl_and_backfill_match_models(postgres_contract):
    factory, _ = postgres_contract
    async with factory.kw["bind"].begin() as connection:
        for sql in SEED:
            await connection.execute(text(sql))
        await connection.execute(
            text("INSERT INTO group_moderation_settings(chat_id,updated_at) VALUES(-100,now())")
        )
        await connection.execute(
            text(
                "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,41,'WARN',900,'Keep PG reason','RECORDED',false,1,'warning-retention',now(),now())"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO group_warning_escalations(id,chat_id,telegram_id,trigger_id,state,created_at,updated_at) VALUES(1,-100,41,1,'FAILED',now(),now())"
            )
        )
        for name in ("warning_limit", "warning_action", "warning_mute_seconds", "warning_version"):
            await connection.execute(
                text("ALTER TABLE group_moderation_settings DROP COLUMN " + name + " CASCADE")
            )
        await connection.execute(
            text("ALTER TABLE group_warning_escalations DROP COLUMN warning_limit CASCADE")
        )
        before = (await connection.execute(text("SELECT * FROM group_sanctions"))).all()

        def upgrade(sync):
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        assert (await connection.execute(text("SELECT * FROM group_sanctions"))).all() == before
        assert (
            await connection.execute(
                text(
                    "SELECT warning_limit,warning_action,warning_mute_seconds,warning_version FROM group_moderation_settings"
                )
            )
        ).one() == (3, "MUTE", 86400, 1)
        assert (
            await connection.execute(text("SELECT warning_limit FROM group_warning_escalations"))
        ).scalar() == 3
        from alembic.autogenerate import compare_metadata

        from app.models import Base

        def drift(sync):
            return compare_metadata(MigrationContext.configure(sync), Base.metadata)

        assert not await connection.run_sync(drift)

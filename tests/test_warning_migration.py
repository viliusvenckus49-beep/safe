"""Additive 0016 -> 0017 retention, drift and safe rollback checks."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, text
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "warning_migration", ROOT / "migrations/versions/0017_warning_escalations_and_controls.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_sqlite_warning_upgrade_preserves_existing_rows_and_refuses_data_loss(tmp_path):
    from test_group_moderation_migration import SEED

    path = tmp_path / "warning-upgrade.db"
    env = {**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///" + str(path)}

    def run(*args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
        )
        assert (result.returncode == 0) == ok, result.stdout + result.stderr

    run("upgrade", "0016")
    with sqlite3.connect(path) as connection:
        for statement in SEED:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,41,'WARN',900,'Retain reason','RECORDED',false,1,'retain-warning',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        tables = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        columns = {
            name: [r[1] for r in connection.execute(f'PRAGMA table_info("{name}")')]
            for name in tables
        }
        before = {
            name: connection.execute(f'SELECT {",".join(columns[name])} FROM "{name}"').fetchall()
            for name in tables
        }
    run("upgrade", "head")
    run("upgrade", "head")
    run("check")
    with sqlite3.connect(path) as connection:
        assert before == {
            name: connection.execute(f'SELECT {",".join(columns[name])} FROM "{name}"').fetchall()
            for name in tables
        }
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute(
            "INSERT INTO group_warning_resets(id,origin_id,actor_id,max_warning_id,expected_count,state,expires_at,created_at) VALUES('test-token',1,900,1,1,'CONFIRMED',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    run("downgrade", "0016", ok=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT state FROM group_warning_resets").fetchone() == (
            "CONFIRMED",
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)


@pytest.mark.asyncio
async def test_postgres_warning_ddl_retention_and_constraints(postgres_contract):
    from test_group_moderation_migration import SEED

    sessions, _ = postgres_contract
    async with sessions.kw["bind"].begin() as connection:
        await connection.execute(text("DROP TABLE group_warning_resets"))
        await connection.execute(text("DROP TABLE group_warning_escalations"))
        for statement in SEED:
            await connection.execute(text(statement))
        tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
        before = {
            name: (await connection.execute(text('SELECT * FROM "' + name + '"'))).all()
            for name in tables
        }

        def upgrade(c):
            with Operations.context(MigrationContext.configure(c)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        assert before == {
            name: (await connection.execute(text('SELECT * FROM "' + name + '"'))).all()
            for name in tables
        }
        assert {"group_warning_resets", "group_warning_escalations"} <= set(
            await connection.run_sync(lambda c: inspect(c).get_table_names())
        )

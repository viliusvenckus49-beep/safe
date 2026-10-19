"""Upgrade existing 0011 sanctions and jobs without rewriting their data."""

import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
LOCAL_SEED = [
    "INSERT INTO group_moderation_settings(chat_id,enabled,bans_enabled,mutes_enabled,version,max_duration_seconds,allow_permanent_bans,updated_at) VALUES(-100,true,true,true,2,2592000,false,CURRENT_TIMESTAMP)",
    "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,until_date,status,owned,policy_version,request_key,created_at,updated_at) VALUES(1,-100,51,'BAN',42,'Existing reason','2026-12-01','ACTIVE',true,2,'old-local-ban',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    "INSERT INTO group_sanction_jobs(sanction_id,operation,actor_id,automatic,status,attempts,available_at,created_at,updated_at) VALUES(1,'APPLY',42,false,'SUCCEEDED',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    "INSERT INTO group_sanction_jobs(sanction_id,operation,automatic,status,attempts,available_at,created_at,updated_at) VALUES(1,'RELEASE',true,'PENDING',0,'2026-12-01',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
]


def test_0012_preserves_0011_rows_and_refuses_destructive_downgrade(tmp_path):
    path = tmp_path / "commands.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}

    def migrate(*args, success=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr

    migrate("upgrade", "0011")
    with sqlite3.connect(path) as connection:
        for sql in SEED + LOCAL_SEED:
            connection.execute(sql)
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
        before = {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables}
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(path) as connection:
        for name in tables:
            assert (
                connection.execute(f'SELECT {",".join(columns[name])} FROM "{name}"').fetchall()
                == before[name]
            )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT warnings_enabled,kicks_enabled,allow_permanent_mutes FROM group_moderation_settings"
        ).fetchone() == (0, 0, 0)
        connection.execute(
            "INSERT INTO group_sanctions(chat_id,telegram_id,kind,actor_id,reason,status,owned,policy_version,request_key,created_at,updated_at) VALUES(-100,52,'MUTE',42,'','ACTIVE',true,2,'permanent-mute',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    migrate("downgrade", "0011", success=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT status FROM group_sanctions WHERE request_key='permanent-mute'"
        ).fetchone() == ("ACTIVE",)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0012",)


@pytest.mark.asyncio
async def test_postgres_0012_preserves_first_stage_sanctions_and_jobs(postgres_contract):
    sessions, _ = postgres_contract
    engine = sessions.kw["bind"]
    spec = importlib.util.spec_from_file_location(
        "migration_0012", ROOT / "migrations/versions/0012_group_commands.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def run(connection, function):
        with Operations.context(MigrationContext.configure(connection)):
            function()

    async with engine.begin() as connection:
        await connection.run_sync(lambda c: run(c, migration.downgrade))
        for sql in SEED + LOCAL_SEED:
            await connection.execute(text(sql))
        before = (await connection.execute(text("SELECT * FROM group_sanctions"))).all()
        jobs = (await connection.execute(text("SELECT * FROM group_sanction_jobs"))).all()
        await connection.run_sync(lambda c: run(c, migration.upgrade))
        assert (await connection.execute(text("SELECT * FROM group_sanctions"))).all() == before
        assert (await connection.execute(text("SELECT * FROM group_sanction_jobs"))).all() == jobs
        assert (
            await connection.execute(
                text(
                    "SELECT warnings_enabled,kicks_enabled,allow_permanent_mutes FROM group_moderation_settings"
                )
            )
        ).all() == [(False, False, False)]

"""0014 -> 0015 preserves every old row and schedule instant on real databases."""

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
from test_ads_publication_migration import ADS_SEED
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "migrations/versions/0015_global_ads_assignments_operations_and_.py"


def test_0015_preserves_old_rows_and_schedule_defaults(tmp_path):
    path = tmp_path / "global.db"
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

    migrate("upgrade", "0014")
    with sqlite3.connect(path) as conn:
        for sql in SEED + ADS_SEED:
            conn.execute(sql)
        conn.execute(
            "INSERT INTO ad_schedules(campaign_id,chat_id,status,interval_seconds,start_at,next_run_at,authorized_by,updated_at) VALUES(1,-100,'ACTIVE',7200,'2026-11-01 10:00:00','2026-11-01 12:00:00',900,CURRENT_TIMESTAMP)"
        )
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        columns = {
            name: [r[1] for r in conn.execute('PRAGMA table_info("' + name + '")')]
            for name in tables
        }
        before = {
            name: conn.execute(
                "SELECT " + ",".join('"' + c + '"' for c in columns[name]) + ' FROM "' + name + '"'
            ).fetchall()
            for name in tables
        }
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(path) as conn:
        for name in tables:
            assert (
                conn.execute(
                    "SELECT "
                    + ",".join('"' + c + '"' for c in columns[name])
                    + ' FROM "'
                    + name
                    + '"'
                ).fetchall()
                == before[name]
            )
        assert conn.execute("SELECT mode,auto_activate FROM ad_assignment_policies").fetchall() == [
            ("SINGLE", 0)
        ]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("downgrade", "0014", success=False)


async def test_postgres_0015_actual_ddl_and_retention(postgres_contract):
    sessions, _ = postgres_contract
    engine = sessions.kw["bind"]
    spec = importlib.util.spec_from_file_location("migration15", FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(connection, function):
        with Operations.context(MigrationContext.configure(connection)):
            function()

    async with engine.begin() as conn:
        await conn.run_sync(lambda c: run(c, module.downgrade))
        for sql in SEED + ADS_SEED:
            await conn.execute(text(sql))
        await conn.execute(
            text(
                "INSERT INTO ad_schedules(campaign_id,chat_id,status,interval_seconds,start_at,next_run_at,authorized_by,updated_at) VALUES(1,-100,'ACTIVE',7200,'2026-11-01 10:00:00+00','2026-11-01 12:00:00+00',900,CURRENT_TIMESTAMP)"
            )
        )
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        columns = await conn.run_sync(
            lambda c: {name: [r["name"] for r in inspect(c).get_columns(name)] for name in tables}
        )
        before = {
            name: (
                await conn.execute(
                    text(
                        "SELECT "
                        + ",".join('"' + v + '"' for v in columns[name])
                        + ' FROM "'
                        + name
                        + '"'
                    )
                )
            ).all()
            for name in tables
        }
        await conn.run_sync(lambda c: run(c, module.upgrade))
        for name in tables:
            assert (
                await conn.execute(
                    text(
                        "SELECT "
                        + ",".join('"' + v + '"' for v in columns[name])
                        + ' FROM "'
                        + name
                        + '"'
                    )
                )
            ).all() == before[name]
        with pytest.raises(RuntimeError, match="GLOBAL ADS data"):
            await conn.run_sync(lambda c: run(c, module.downgrade))

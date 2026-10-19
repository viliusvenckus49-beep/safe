"""Actual additive 0013 -> 0014 DDL, snapshots, constraints and safe rollback."""

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
from test_ads_customers_migration import remove_customer_schema
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
ADS_SEED = [
    "INSERT INTO ad_campaigns(id,internal_name,created_by,updated_by,request_key,created_at,updated_at) VALUES(1,'Preserve',900,900,'ads-seed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    "INSERT INTO ad_content_versions(campaign_id,revision,content,actor_id,request_key,created_at) VALUES(1,1,'{}',900,'ads-content',CURRENT_TIMESTAMP)",
]


def test_0014_preserves_old_data_and_refuses_history_loss(tmp_path):
    path = tmp_path / "publication.db"
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

    migrate("upgrade", "0013")
    with sqlite3.connect(path) as connection:
        for sql in SEED + ADS_SEED:
            connection.execute(sql)
        tables = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        before = {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables}
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(path) as connection:
        for name in tables:
            assert connection.execute(f'SELECT * FROM "{name}"').fetchall() == before[name]
        connection.execute(
            "INSERT INTO ad_schedules(campaign_id,authorized_by,updated_at) VALUES(1,900,CURRENT_TIMESTAMP)"
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    migrate("downgrade", "0013", success=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM ad_schedules").fetchone() == (1,)
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0015",)


@pytest.mark.asyncio
async def test_postgres_0014_ddl_and_all_legacy_rows(postgres_contract):
    sessions, _ = postgres_contract
    engine = sessions.kw["bind"]
    spec = importlib.util.spec_from_file_location(
        "migration14", ROOT / "migrations/versions/0014_ad_publication.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(connection, function):
        with Operations.context(MigrationContext.configure(connection)):
            function()

    async with engine.begin() as connection:
        await connection.run_sync(remove_customer_schema)
        global_spec = importlib.util.spec_from_file_location(
            "global_migration",
            ROOT / "migrations/versions/0015_global_ads_assignments_operations_and_.py",
        )
        global_module = importlib.util.module_from_spec(global_spec)
        global_spec.loader.exec_module(global_module)
        await connection.run_sync(lambda c: run(c, global_module.downgrade))
        await connection.run_sync(lambda c: run(c, module.downgrade))
        for sql in SEED + ADS_SEED:
            await connection.execute(text(sql))
        tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
        before = {
            name: (await connection.execute(text(f'SELECT * FROM "{name}"'))).all()
            for name in tables
        }
        await connection.run_sync(lambda c: run(c, module.upgrade))
        for name in tables:
            assert (await connection.execute(text(f'SELECT * FROM "{name}"'))).all() == before[name]
        await connection.execute(
            text(
                "INSERT INTO ad_schedules(campaign_id,authorized_by,updated_at) VALUES(1,900,CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="history"):
            await connection.run_sync(lambda c: run(c, module.downgrade))
        assert await connection.scalar(text("SELECT count(*) FROM ad_schedules")) == 1

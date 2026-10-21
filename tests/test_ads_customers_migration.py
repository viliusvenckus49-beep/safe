"""Actual additive 0016 DDL, legacy row preservation and guarded rollback."""

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
SPEC = importlib.util.spec_from_file_location(
    "migration16", ROOT / "migrations/versions/0016_ad_customer_capabilities_and_hashed_.py"
)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


def remove_customer_schema(connection):
    # Tests of older DDL must first remove the new dependent invitation table.
    with Operations.context(MigrationContext.configure(connection)):
        if "ad_client_invitations" in inspect(connection).get_table_names():
            spec = importlib.util.spec_from_file_location(
                "ads20", ROOT / "migrations/versions/0020_unified_auto_ads.py"
            )
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.downgrade()
        MIGRATION.downgrade()


def test_sqlite_0016_retains_every_legacy_row_and_repeated_upgrade(tmp_path):
    from test_ads_publication_migration import ADS_SEED
    from test_group_moderation_migration import SEED

    path = tmp_path / "customers.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}

    def run(*args, ok=True):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert (result.returncode == 0) == ok, result.stdout + result.stderr

    run("upgrade", "0015")
    with sqlite3.connect(path) as conn:
        for sql in SEED + ADS_SEED:
            conn.execute(sql)
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        before = {name: conn.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables}
    run("upgrade", "head")
    run("upgrade", "head")
    run("check")
    with sqlite3.connect(path) as conn:
        assert {
            name: conn.execute(f'SELECT * FROM "{name}"').fetchall() for name in tables
        } == before
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.execute(
            "INSERT INTO ad_customer_grants(campaign_id,customer_id,status,assigned_by,created_at) VALUES(1,42,'ACTIVE',900,CURRENT_TIMESTAMP)"
        )
    run("downgrade", "0015", ok=False)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT customer_id FROM ad_customer_grants").fetchall() == [(42,)]
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("0016",)


async def test_postgres_0016_retention_constraints_and_guard(postgres_contract):
    from test_ads_publication_migration import ADS_SEED
    from test_group_moderation_migration import SEED

    sessions, _ = postgres_contract
    async with sessions.kw["bind"].begin() as conn:
        await conn.run_sync(remove_customer_schema)
        for sql in SEED + ADS_SEED:
            await conn.execute(text(sql))
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
        before = {
            name: (await conn.execute(text(f'SELECT * FROM "{name}"'))).all() for name in tables
        }

        def upgrade(c):
            with Operations.context(MigrationContext.configure(c)):
                MIGRATION.upgrade()

        await conn.run_sync(upgrade)
        assert {
            name: (await conn.execute(text(f'SELECT * FROM "{name}"'))).all() for name in tables
        } == before
        await conn.execute(
            text(
                "INSERT INTO ad_customer_grants(campaign_id,customer_id,status,assigned_by,created_at) VALUES(1,42,'ACTIVE',900,CURRENT_TIMESTAMP)"
            )
        )
        with pytest.raises(RuntimeError, match="Customer ADS data"):
            await conn.run_sync(remove_customer_schema)
        assert await conn.scalar(text("SELECT count(*) FROM ad_customer_grants")) == 1

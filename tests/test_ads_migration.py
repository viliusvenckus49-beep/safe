"""0012 -> 0013 preserves legacy rows and refuses to discard real ad content."""

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
from test_group_commands_migration import LOCAL_SEED
from test_group_moderation_migration import SEED
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_upgrade_preserves_existing_data_and_safe_downgrade_guard(tmp_path):
    path = tmp_path / "ads.db"
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

    migrate("upgrade", "0012")
    with sqlite3.connect(path) as connection:
        for sql in SEED + LOCAL_SEED:
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
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute(
            "INSERT INTO ad_campaigns(internal_name,created_by,updated_by,request_key,created_at,updated_at) VALUES('Keep ad',900,900,'keep-ad',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    migrate("downgrade", "0012", success=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT internal_name FROM ad_campaigns").fetchone() == (
            "Keep ad",
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0013",)


@pytest.mark.asyncio
async def test_postgres_actual_migration_preserves_legacy_schema_and_rows(postgres_contract):
    sessions, _ = postgres_contract
    engine = sessions.kw["bind"]
    spec = importlib.util.spec_from_file_location(
        "ads_migration", ROOT / "migrations/versions/0013_ad_content.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(connection, function):
        with Operations.context(MigrationContext.configure(connection)):
            function()

    async with engine.begin() as connection:
        publication = importlib.util.spec_from_file_location(
            "publication_migration", ROOT / "migrations/versions/0014_ad_publication.py"
        )
        publication_module = importlib.util.module_from_spec(publication)
        publication.loader.exec_module(publication_module)
        await connection.run_sync(lambda c: run(c, publication_module.downgrade))
        await connection.run_sync(lambda c: run(c, module.downgrade))
        for sql in SEED + LOCAL_SEED:
            await connection.execute(text(sql))
        tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
        before = {
            name: (await connection.execute(text(f'SELECT * FROM "{name}"'))).all()
            for name in tables
        }
        await connection.run_sync(lambda c: run(c, module.upgrade))
        for name in tables:
            assert (await connection.execute(text(f'SELECT * FROM "{name}"'))).all() == before[name]
        assert await connection.scalar(text("SELECT count(*) FROM ad_campaigns")) == 0

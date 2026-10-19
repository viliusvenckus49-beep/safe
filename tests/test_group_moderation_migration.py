"""Exercise 0010 -> 0011 on disposable databases, including existing data snapshots."""

import asyncio
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_engine_from_config, create_async_engine

ROOT = Path(__file__).resolve().parents[1]
NEW_TABLES = {
    "group_moderation_settings",
    "group_sanctions",
    "group_sanction_jobs",
    "group_moderation_drafts",
}
SEED = [
    "INSERT INTO users(id,telegram_id,username,display_name,language,created_at,updated_at) "
    "VALUES(1,41,'original','Original','ru',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),"
    "(2,42,'trusted','Trusted','en',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
    "INSERT INTO reputation_events(giver_user_id,receiver_user_id,value,request_key,created_at) "
    "VALUES(1,2,1,'keep-rep',CURRENT_TIMESTAMP)",
    "INSERT INTO scam_records(target_id,reason,status,moderator_id,created_at) "
    "VALUES(1,'Keep scam','ACTIVE',900,CURRENT_TIMESTAMP)",
    "INSERT INTO trusted_designations(target_id,active,actor_id,updated_at) "
    "VALUES(2,true,900,CURRENT_TIMESTAMP)",
    "INSERT INTO username_history(user_id,username,observed_at) "
    "VALUES(1,'old_username',CURRENT_TIMESTAMP)",
    "INSERT INTO profile_activity(chat_id,message_id,user_id,active_date) "
    "VALUES(-100,7,1,'2026-10-01')",
    "INSERT INTO managed_groups(chat_id,chat_type,title,enabled,approved,can_restrict_members,updated_at) "
    "VALUES(-100,'supergroup','Approved',true,true,true,CURRENT_TIMESTAMP),"
    "(-200,'supergroup','Unapproved',true,false,true,CURRENT_TIMESTAMP)",
    "INSERT INTO audit_events(actor_id,action,details,created_at) "
    "VALUES(900,'existing','{}',CURRENT_TIMESTAMP)",
]


def test_additive_migration_preserves_every_legacy_table(tmp_path):
    database = tmp_path / "stage-one.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0010")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        for statement in SEED:
            connection.execute(statement)
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
                "('alembic_version','sqlite_sequence')"
            )
        ]
        before = {
            table: connection.execute(f'SELECT * FROM "{table}"').fetchall() for table in tables
        }
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == before[table]
        assert connection.execute(
            "SELECT chat_id,enabled,bans_enabled,mutes_enabled,allow_permanent_bans "
            "FROM group_moderation_settings"
        ).fetchall() == [(-100, 0, 0, 0, 0)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE group_moderation_settings SET max_duration_seconds=1")
        for key, status in [("first", "PENDING"), ("second", "PENDING")]:
            statement = (
                "INSERT INTO group_sanctions(chat_id,telegram_id,kind,actor_id,reason,"
                "status,policy_version,request_key,created_at,updated_at) "
                "VALUES(-100,51,'BAN',42,'reason',?,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
            if key == "first":
                connection.execute(statement, (status, key))
            else:
                with pytest.raises(sqlite3.IntegrityError):
                    connection.execute(statement, (status, key))
        connection.execute("UPDATE group_sanctions SET status='REVOKED'")
        connection.execute(statement, ("PENDING", "second"))
    migrate("downgrade", "0010")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert connection.execute(f'SELECT * FROM "{table}"').fetchall() == before[table]
        remaining = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert not (NEW_TABLES & remaining)


@pytest.mark.asyncio
async def test_postgres_actual_migration_preserves_legacy_data(monkeypatch):
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for isolated real Alembic PostgreSQL upgrade")
    # No production URL fallback. Only this random disposable schema is touched.
    schema = "safecheck_migration_" + uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    admin = create_async_engine(url)
    monkeypatch.setenv("DATABASE_URL", url)

    def migrate(*args):
        def isolated_engine(*engine_args, **kwargs):
            kwargs["connect_args"] = {"server_settings": {"search_path": schema}}
            return async_engine_from_config(*engine_args, **kwargs)

        with patch("sqlalchemy.ext.asyncio.async_engine_from_config", isolated_engine):
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            getattr(command, args[0])(config, *args[1:])

    async def snapshot():
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
            return {
                table: (await connection.execute(text(f'SELECT * FROM "{table}"'))).all()
                for table in tables
                if table not in NEW_TABLES | {"alembic_version"}
            }

    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        await asyncio.to_thread(migrate, "upgrade", "0010")
        async with engine.begin() as connection:
            for statement in SEED:
                await connection.execute(text(statement))
        before = await snapshot()
        await asyncio.to_thread(migrate, "upgrade", "head")
        await asyncio.to_thread(migrate, "check")
        assert await snapshot() == before
        async with engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT chat_id,enabled,bans_enabled,mutes_enabled FROM group_moderation_settings"
                    )
                )
            ).all()
            assert rows == [(-100, False, False, False)]
        await asyncio.to_thread(migrate, "downgrade", "0010")
        assert await snapshot() == before
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()

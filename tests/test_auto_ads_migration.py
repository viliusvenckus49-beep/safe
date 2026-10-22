"""Actual Alembic upgrade of legacy ads; no production connection is used."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]


def migrate(path, *args, success=True):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"},
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr


def test_0020_preserves_legacy_content_customers_and_snapshot_audience(tmp_path):
    path = tmp_path / "ads.db"
    migrate(path, "upgrade", "0019")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO ad_campaigns(id,internal_name,created_by,updated_by,request_key,created_at,updated_at) VALUES(1,'Legacy',900,900,'legacy',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO ad_content_versions(campaign_id,revision,content,actor_id,request_key,created_at) VALUES(1,1,'{}',900,'content',CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO ad_assignment_policies(campaign_id,mode,authorized_by,updated_at) VALUES(1,'FUTURE',900,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO managed_groups(chat_id,title,approved,enabled,can_restrict_members,updated_at,chat_type) VALUES(-100,'Existing',1,1,1,CURRENT_TIMESTAMP,'supergroup')"
        )
        connection.execute(
            "INSERT INTO ad_schedules(campaign_id,chat_id,interval_seconds,authorized_by,updated_at) VALUES(1,-100,7200,900,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO ad_customer_grants(campaign_id,customer_id,status,assigned_by,created_at) VALUES(1,42,'ACTIVE',900,CURRENT_TIMESTAMP)"
        )
        names = ("ad_campaigns", "ad_content_versions", "ad_schedules", "ad_customer_grants")
        before = {name: connection.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}
    migrate(path, "upgrade", "head")
    migrate(path, "check")
    with sqlite3.connect(path) as connection:
        for name, values in before.items():
            assert connection.execute(f'SELECT * FROM "{name}"').fetchall() == values
        assert connection.execute(
            "SELECT mode,auto_activate,version FROM ad_assignment_policies"
        ).fetchone() == ("EXISTING", 0, 2)
        assert connection.execute("SELECT action FROM audit_events").fetchone() == (
            "ads_audience_snapshot_migrated",
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0021",)
        connection.execute(
            "INSERT INTO ad_client_invitations(campaign_id,token_hash,status,expected_grant,created_by,created_at,expires_at) VALUES(1,?, 'ACTIVE','0.0',900,CURRENT_TIMESTAMP,datetime('now','+1 day'))",
            ("a" * 64,),
        )
    migrate(path, "downgrade", "0019", success=False)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT count(*) FROM ad_client_invitations").fetchone() == (1,)


async def test_postgres_0020_matches_models_and_retains_constraints(postgres_contract):
    sessions, settings = postgres_contract
    engine = sessions.kw["bind"]
    async with engine.begin() as connection:

        def check(sync):
            inspector = inspect(sync)
            assert "ad_client_invitations" in inspector.get_table_names()
            constraints = {
                c["name"] for c in inspector.get_check_constraints("ad_client_invitations")
            }
            assert {
                "ck_ad_invite_status",
                "ck_ad_invite_consumption",
                "ck_ad_invite_expiry",
            } <= constraints
            kinds = next(
                c
                for c in inspector.get_check_constraints("ad_publication_parts")
                if c["name"] == "ck_ad_part_kind"
            )
            assert "ANIMATION" in kinds["sqltext"] and "DOCUMENT" in kinds["sqltext"]

        await connection.run_sync(check)


async def test_postgres_actual_0020_ddl_upgrade_preserves_existing_ads(postgres_contract):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from test_auto_ads_unified import assign, create

    sessions, settings = postgres_contract
    engine = sessions.kw["bind"]
    spec = importlib.util.spec_from_file_location(
        "ads20", ROOT / "migrations/versions/0020_unified_auto_ads.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def run(connection, function):
        with Operations.context(MigrationContext.configure(connection)):
            function()

    async with engine.begin() as connection:
        await connection.run_sync(run, module.downgrade)
    cid = await create(sessions, settings)
    await assign(sessions, settings, cid)
    async with engine.begin() as connection:
        before = (await connection.execute(text("SELECT * FROM ad_campaigns"))).all()
        grants = (await connection.execute(text("SELECT * FROM ad_customer_grants"))).all()
        await connection.run_sync(run, module.upgrade)
        assert (await connection.execute(text("SELECT * FROM ad_campaigns"))).all() == before
        assert (await connection.execute(text("SELECT * FROM ad_customer_grants"))).all() == grants

"""Check actual migrations, rather than relying only on ORM create_all."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_migrations_match_models_and_are_reversible(tmp_path):
    database = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def alembic(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    alembic("upgrade", "head")
    assert "No new upgrade operations" in alembic("check")
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "users",
            "reputation_events",
            "reports",
            "report_evidence",
            "scam_records",
            "moderation_actions",
            "audit_events",
            "username_history",
            "operation_locks",
        } <= tables
    alembic("downgrade", "base")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
            is None
        )
    alembic("upgrade", "head")


def test_reputation_upgrade_preserves_existing_approved_events(tmp_path):
    database = tmp_path / "upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def upgrade(revision):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    upgrade("0002_identity_lock")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users(id,telegram_id,display_name,created_at,updated_at) VALUES(1,41,'Giver',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),(2,42,'Receiver',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO reputation_events(giver_user_id,receiver_user_id,value,request_key,created_at) VALUES(1,2,1,'old-vote',CURRENT_TIMESTAMP)"
        )
    upgrade("head")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value,request_key FROM reputation_events").fetchall() == [
            (1, "old-vote")
        ]
        assert connection.execute("SELECT count(*) FROM reputation_requests").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM reputation_adjustments").fetchone() == (0,)


def test_language_upgrade_preserves_existing_records(tmp_path):
    database = tmp_path / "language-upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0004")
    with sqlite3.connect(database) as connection:
        connection.executescript("""
        INSERT INTO users(id,telegram_id,display_name,created_at,updated_at)
        VALUES(1,41,'Giver',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP),(2,42,'Receiver',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);
        INSERT INTO reputation_events(giver_user_id,receiver_user_id,value,request_key,created_at)
        VALUES(1,2,1,'preserved-rep',CURRENT_TIMESTAMP);
        INSERT INTO reports(id,reference,reporter_id,target_id,reason,status,request_key,created_at)
        VALUES(1,'SC-2026-000001',1,2,'Original reason','PENDING','preserved-report',CURRENT_TIMESTAMP);
        INSERT INTO scam_records(target_id,reason,status,moderator_id,created_at)
        VALUES(2,'Original scam reason','ACTIVE',900,CURRENT_TIMESTAMP);
        INSERT INTO username_history(user_id,username,observed_at) VALUES(2,'original_name',CURRENT_TIMESTAMP);
        INSERT INTO audit_events(actor_id,action,target_id,details,created_at)
        VALUES(900,'scam_added',2,'{}',CURRENT_TIMESTAMP);
        """)
        tables = [
            "users",
            "reputation_events",
            "reports",
            "scam_records",
            "username_history",
            "audit_events",
        ]
        columns = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
        before = {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in tables
        }
    migrate("upgrade", "head")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert (
                connection.execute(
                    f"SELECT {','.join(columns[table])} FROM {table} ORDER BY id"
                ).fetchall()
                == before[table]
            )
        assert connection.execute("SELECT language FROM users").fetchall() == [(None,), (None,)]
        connection.execute("UPDATE users SET language='en' WHERE id=1")
        try:
            connection.execute("UPDATE users SET language='de' WHERE id=2")
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Invalid language accepted")
    migrate("check")
    migrate("downgrade", "0004")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert (
                connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == before[table]
            )


def test_group_approval_upgrade_preserves_members_and_recovery(tmp_path):
    database = tmp_path / "group-approval.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0005")
    with sqlite3.connect(database) as connection:
        connection.executescript("""
        INSERT INTO managed_groups(chat_id,chat_type,title,enabled,can_restrict_members,updated_at) VALUES(-100,'supergroup','Original',1,1,CURRENT_TIMESTAMP);
        INSERT INTO private_contacts(telegram_id,started_at) VALUES(42,CURRENT_TIMESTAMP);
        INSERT INTO observed_members(chat_id,telegram_id,display_name,observed_at) VALUES(-100,42,'Member',CURRENT_TIMESTAMP);
        INSERT INTO recovery_subscriptions(chat_id,telegram_id,consent,updated_at) VALUES(-100,42,1,CURRENT_TIMESTAMP);
        INSERT INTO recovery_campaigns(id,chat_id,actor_id,invite_url,request_key,created_at) VALUES(1,-100,900,'https://t.me/+Invite123','original',CURRENT_TIMESTAMP);
        INSERT INTO recovery_deliveries(campaign_id,telegram_id,status,attempts,next_attempt_at) VALUES(1,42,'PENDING',0,CURRENT_TIMESTAMP);
        """)
        tables = [
            "managed_groups",
            "private_contacts",
            "observed_members",
            "recovery_subscriptions",
            "recovery_campaigns",
            "recovery_deliveries",
        ]
        columns = {
            table: [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
            for table in tables
        }
        before = {
            table: connection.execute(f"SELECT * FROM {table}").fetchall() for table in tables
        }
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert (
                connection.execute(f"SELECT {','.join(columns[table])} FROM {table}").fetchall()
                == before[table]
            )
        assert connection.execute("SELECT approved FROM managed_groups").fetchone() == (1,)
        connection.execute(
            "INSERT INTO managed_groups(chat_id,chat_type,title,enabled,can_restrict_members,updated_at) VALUES(-200,'supergroup','New',0,0,CURRENT_TIMESTAMP)"
        )
        assert connection.execute(
            "SELECT approved FROM managed_groups WHERE chat_id=-200"
        ).fetchone() == (0,)
        connection.execute("DELETE FROM managed_groups WHERE chat_id=-200")
    migrate("downgrade", "0005")
    with sqlite3.connect(database) as connection:
        for table in tables:
            assert connection.execute(f"SELECT * FROM {table}").fetchall() == before[table]


def test_administrator_migration_preserves_existing_data(tmp_path):
    database = tmp_path / "admin-upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0006")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users(id,telegram_id,display_name,language,created_at,updated_at) VALUES(1,42,'Existing','ru',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO audit_events(actor_id,action,details,created_at) VALUES(900,'existing','{}',CURRENT_TIMESTAMP)"
        )
        before = connection.execute("SELECT * FROM users").fetchall()
        audits = connection.execute("SELECT * FROM audit_events").fetchall()
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == before
        assert connection.execute("SELECT * FROM audit_events").fetchall() == audits
        assert connection.execute("SELECT count(*) FROM administrators").fetchone() == (0,)
    migrate("downgrade", "0006")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == before
        assert connection.execute("SELECT * FROM audit_events").fetchall() == audits


def test_trusted_migration_preserves_data(tmp_path):
    database = tmp_path / "trusted-upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0007")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users(id,telegram_id,display_name,language,created_at,updated_at) VALUES(1,42,'Existing','ru',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO administrators(telegram_id,active,assigned_by,updated_at) VALUES(901,1,900,CURRENT_TIMESTAMP)"
        )
        before = connection.execute("SELECT * FROM users").fetchall()
        admins = connection.execute("SELECT * FROM administrators").fetchall()
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == before
        assert connection.execute("SELECT * FROM administrators").fetchall() == admins
        assert connection.execute("SELECT count(*) FROM trusted_designations").fetchone() == (0,)
    migrate("downgrade", "0007")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT * FROM users").fetchall() == before
        assert connection.execute("SELECT * FROM administrators").fetchall() == admins


def test_rep_comment_upgrade_preserves_pending_legacy_request(tmp_path):
    database = tmp_path / "rep-comments.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    root = Path(__file__).resolve().parents[1]

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0008")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO users(id,telegram_id,display_name,created_at,updated_at) VALUES(1,1,'Giver','2026-01-01','2026-01-01'),(2,2,'Receiver','2026-01-01','2026-01-01')"
        )
        connection.execute(
            "INSERT INTO reputation_requests(id,reference,giver_user_id,receiver_user_id,value,request_key,status,notes,created_at) VALUES(1,'RP-2026-000001',1,2,1,'legacy','PENDING','','2026-01-01')"
        )
    migrate("upgrade", "head")
    migrate("check")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reference,status,comment FROM reputation_requests"
        ).fetchall() == [("RP-2026-000001", "PENDING", None)]
        assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE reputation_requests SET comment='abcd'")
        for statement in (
            "UPDATE reputation_requests SET value=0",
            "UPDATE reputation_requests SET status='BAD'",
            "UPDATE reputation_requests SET giver_user_id=receiver_user_id",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement)
    migrate("downgrade", "0008")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT reference,status FROM reputation_requests"
        ).fetchall() == [("RP-2026-000001", "PENDING")]

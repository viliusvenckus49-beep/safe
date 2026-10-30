"""Apply real Alembic DDL to retained data; populated catalog rollback fails closed."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_0024_is_additive_preserves_existing_identity_and_refuses_data_loss(tmp_path):
    path = tmp_path / "merchant-upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}
    root = Path(__file__).resolve().parents[1]

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )

    assert run("upgrade", "0023").returncode == 0
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO users (id,telegram_id,username,display_name,created_at,updated_at) VALUES (1,42,'original','Original',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    upgrade = run("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr
    check = run("check")
    assert check.returncode == 0, check.stdout + check.stderr
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT id,telegram_id,username FROM users").fetchall() == [
            (1, 42, "original")
        ]
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == ("0025",)
        assert c.execute("SELECT count(*) FROM merchant_entries").fetchone() == (0,)
        c.execute(
            "INSERT INTO merchant_entries (owner_user_id,status,version,created_at,updated_at) VALUES (1,'DRAFT',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    down = run("downgrade", "0023")
    assert down.returncode != 0 and "Preserve merchant" in down.stderr
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM merchant_entries").fetchone() == (1,)
        assert c.execute("SELECT telegram_id FROM users").fetchone() == (42,)


def test_0025_preserves_all_0024_columns_identity_proposals_and_retains_new_contacts(tmp_path):
    path = tmp_path / "contact-upgrade.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"}
    root = Path(__file__).resolve().parents[1]

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )

    assert run("upgrade", "0024").returncode == 0
    with sqlite3.connect(path) as c:
        c.execute(
            "INSERT INTO users (id,telegram_id,username,display_name,created_at,updated_at) VALUES (1,42,'original','Original',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        c.execute(
            "INSERT INTO merchant_entries (id,owner_user_id,name,name_key,city_code,description,status,version,created_at,updated_at,approved_at,approved_by) VALUES (1,1,'Existing Merchant','existing merchant','siauliai','Aprašymas <&>\n\nAntra pastraipa.','APPROVED',7,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,900)"
        )
        c.execute(
            "INSERT INTO merchant_proposals (entry_id,actor_id,kind,status,name,base_version,version,request_key,created_at,updated_at,expires_at) VALUES (1,42,'EDIT','PENDING','Unpublished change',7,2,'historical-proposal',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
        tables = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('alembic_version','sqlite_sequence')"
            )
        ]
        columns = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in tables}
        before = {t: c.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}
    upgraded = run("upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    checked = run("check")
    assert checked.returncode == 0, checked.stderr
    with sqlite3.connect(path) as c:
        for table in tables:
            names = ",".join('"' + col + '"' for col in columns[table])
            assert c.execute(f'SELECT {names} FROM "{table}"').fetchall() == before[table]
        assert c.execute("SELECT contact FROM merchant_entries").fetchall() == [(None,)]
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        c.execute("UPDATE merchant_entries SET contact='https://t.me/public_contact'")
    down = run("downgrade", "0024")
    assert down.returncode != 0 and "Preserve merchant contacts" in down.stderr
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT contact FROM merchant_entries").fetchone() == (
            "https://t.me/public_contact",
        )
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == ("0025",)

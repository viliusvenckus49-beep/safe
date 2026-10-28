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
        assert c.execute("SELECT version_num FROM alembic_version").fetchone() == ("0024",)
        assert c.execute("SELECT count(*) FROM merchant_entries").fetchone() == (0,)
        c.execute(
            "INSERT INTO merchant_entries (owner_user_id,status,version,created_at,updated_at) VALUES (1,'DRAFT',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        )
    down = run("downgrade", "0023")
    assert down.returncode != 0 and "Preserve merchant" in down.stderr
    with sqlite3.connect(path) as c:
        assert c.execute("SELECT count(*) FROM merchant_entries").fetchone() == (1,)
        assert c.execute("SELECT telegram_id FROM users").fetchone() == (42,)

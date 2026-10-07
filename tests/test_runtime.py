"""Startup errors must not reveal input values, including model-level failures."""

import pytest

from app import main
from app.config import Settings


def test_startup_model_validation_redacts_secrets(monkeypatch, capsys):
    monkeypatch.setenv("BOT_TOKEN", "123456:SECRET_SENTINEL")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///test.db")
    monkeypatch.setenv("ADMIN_IDS", "")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(main, "Settings", lambda: Settings(_env_file=None))
    with pytest.raises(SystemExit) as error:
        main.run()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "environment" in output
    assert "SECRET_SENTINEL" not in output
    assert "Traceback" not in output


def test_startup_field_validation_redacts_database_password(monkeypatch, capsys):
    monkeypatch.setenv("BOT_TOKEN", "123456:SECRET_SENTINEL")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:PASSWORD_SENTINEL@host/db")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(main, "Settings", lambda: Settings(_env_file=None))
    with pytest.raises(SystemExit) as error:
        main.run()
    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "database_url" in output
    assert "PASSWORD_SENTINEL" not in output
    assert "SECRET_SENTINEL" not in output

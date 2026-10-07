import pytest
from pydantic import ValidationError

from app.config import Settings


def test_admin_ids_numeric_and_secret_redacted(settings):
    assert settings.admins == frozenset({900})
    assert "TEST_ONLY" not in repr(settings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("bot_token", "oops"),
        ("admin_ids", "@admin"),
        ("admin_ids", "-1"),
        ("database_url", "postgresql://bad"),
    ],
)
def test_invalid_settings_fail_fast(field, value):
    args = {"bot_token": "123:TEST_ONLY", "_env_file": None, field: value}
    with pytest.raises(ValidationError):
        Settings(**args)


def test_group_owner_single_admin_fallback_and_multi_admin_fail_closed(settings):
    assert settings.group_owner == 900
    settings.admin_ids = "900,901"
    assert settings.group_owner is None
    settings.group_owner_id = 900
    assert settings.group_owner == 900


@pytest.mark.parametrize("owner", [0, -1, 901, "@username"])
def test_group_owner_must_be_positive_configured_admin(owner):
    with pytest.raises(ValidationError):
        Settings(bot_token="123:TEST_ONLY", admin_ids="900", group_owner_id=owner, _env_file=None)


@pytest.mark.parametrize("admin_ids", ["²", "١", "１２", "9223372036854775808"])
def test_admin_config_ids_are_ascii_and_fit_database(admin_ids):
    with pytest.raises(ValidationError):
        Settings(bot_token="123:TEST_ONLY", admin_ids=admin_ids, _env_file=None)

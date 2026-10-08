from types import SimpleNamespace

import pytest

from deploy.mtproto_login import (
    STAFF_BOT,
    STAFF_CHAT_ID,
    credentials,
    private_directory,
    private_file,
    validate_credentials,
    verify_staff,
)


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"api_id": True, "api_hash": "a" * 32},
        {"api_id": 0, "api_hash": "a" * 32},
        {"api_id": 1, "api_hash": "bad"},
        {"api_id": 1, "api_hash": "a" * 32, "unexpected": "value"},
    ],
)
def test_login_rejects_invalid_configuration(values):
    with pytest.raises(ValueError):
        validate_credentials(values)


def test_credentials_are_private_and_reused_without_reprompt(tmp_path, monkeypatch):
    path = tmp_path / "state"
    private_directory(path)
    assert path.stat().st_mode & 0o777 == 0o700
    monkeypatch.setattr("builtins.input", lambda prompt: "12345")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "a" * 32)
    expected = credentials(path / "api.json")
    assert (path / "api.json").stat().st_mode & 0o777 == 0o600

    def unexpected(prompt):
        raise AssertionError("Existing configuration must not prompt")

    monkeypatch.setattr("builtins.input", unexpected)
    monkeypatch.setattr("getpass.getpass", unexpected)
    assert credentials(path / "api.json") == expected


def test_login_rejects_symlink_state_and_files(tmp_path):
    destination = tmp_path / "private"
    destination.mkdir()
    link = tmp_path / "link"
    link.symlink_to(destination)
    with pytest.raises(ValueError):
        private_directory(link)
    file = tmp_path / "api.json"
    file.symlink_to(tmp_path / "missing.json")
    with pytest.raises(OSError):
        private_file(file)
    assert not (tmp_path / "missing.json").exists()


class StaffClient:
    def __init__(self, *, account_bot=False, staff_bot=True, joined=True):
        self.me = SimpleNamespace(id=42, bot=account_bot)
        self.bot = SimpleNamespace(id=99, bot=staff_bot, username=STAFF_BOT)
        self.joined = joined
        self.calls = []

    async def get_me(self):
        return self.me

    async def get_entity(self, value):
        self.calls.append(value)
        return SimpleNamespace(id=STAFF_CHAT_ID) if value == STAFF_CHAT_ID else self.bot

    async def get_participants(self, staff):
        return [self.me, self.bot] if self.joined else [self.me]


@pytest.mark.asyncio
async def test_session_check_only_reads_staff_and_never_sends_ban():
    client = StaffClient()
    assert await verify_staff(client)
    assert client.calls == [STAFF_CHAT_ID, STAFF_BOT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options", [{"account_bot": True}, {"staff_bot": False}, {"joined": False}]
)
async def test_session_check_rejects_wrong_account_or_staff_bot(options):
    with pytest.raises(ValueError):
        await verify_staff(StaffClient(**options))

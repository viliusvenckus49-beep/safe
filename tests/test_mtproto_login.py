import argparse
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "mtproto_login", Path(__file__).resolve().parents[1] / "deploy/mtproto_login.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
STAFF_BOT = module.STAFF_BOT
STAFF_CHAT_ID = module.STAFF_CHAT_ID
credentials = module.credentials
private_directory = module.private_directory
private_file = module.private_file
validate_credentials = module.validate_credentials
verify_staff = module.verify_staff


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
    def __init__(
        self,
        *,
        account_bot=False,
        staff_bot=True,
        joined=True,
        staff_id=STAFF_CHAT_ID,
        bot_username=STAFF_BOT,
    ):
        self.me = SimpleNamespace(id=42, bot=account_bot)
        self.bot = SimpleNamespace(id=99, bot=staff_bot, username=bot_username)
        self.staff_id = staff_id
        self.joined = joined
        self.calls = []

    async def get_me(self):
        return self.me

    async def get_entity(self, value):
        self.calls.append(value)
        return SimpleNamespace(id=self.staff_id) if value == self.staff_id else self.bot

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


class FreshStaffClient(StaffClient):
    def __init__(self, dialogs, **options):
        super().__init__(**options)
        self.dialogs = dialogs
        self.seen_staff = None

    async def get_entity(self, value):
        if value == self.staff_id:
            self.calls.append(value)
            raise ValueError("Could not find the input entity")
        return await super().get_entity(value)

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog

    async def get_participants(self, staff):
        self.seen_staff = staff
        return await super().get_participants(staff)


async def test_new_account_finds_the_exact_private_staff_entity_in_dialogs():
    impostor = SimpleNamespace(id=STAFF_CHAT_ID - 1, title="Crimson Staff")
    staff = SimpleNamespace(id=4300060813, left=False)
    client = FreshStaffClient(
        [
            SimpleNamespace(id=STAFF_CHAT_ID - 1, entity=impostor),
            SimpleNamespace(id=STAFF_CHAT_ID, entity=staff),
        ]
    )
    assert await verify_staff(client)
    assert client.seen_staff is staff
    assert client.calls == [STAFF_CHAT_ID, STAFF_BOT]


async def test_new_account_cannot_select_a_different_group_with_the_same_title():
    client = FreshStaffClient(
        [
            SimpleNamespace(
                id=STAFF_CHAT_ID - 1,
                entity=SimpleNamespace(id=STAFF_CHAT_ID - 1, title="Crimson Staff"),
            )
        ]
    )
    with pytest.raises(ValueError, match="not in the account's dialogs"):
        await verify_staff(client)
    assert client.seen_staff is None


@pytest.mark.parametrize("flags", [{"left": True}, {"deactivated": True}])
async def test_new_account_must_still_be_a_member_of_the_staff_group(flags):
    client = FreshStaffClient(
        [SimpleNamespace(id=STAFF_CHAT_ID, entity=SimpleNamespace(id=4300060813, **flags))]
    )
    with pytest.raises(ValueError, match="Staff group membership"):
        await verify_staff(client)


async def test_new_account_fallback_still_requires_the_staff_bot():
    client = FreshStaffClient(
        [SimpleNamespace(id=STAFF_CHAT_ID, entity=SimpleNamespace(id=4300060813))],
        joined=False,
    )
    with pytest.raises(ValueError, match="Staff bot is not"):
        await verify_staff(client)


async def test_switching_staff_verifies_only_the_selected_group_and_bot():
    selected_id = -1004430959898
    selected_bot = "OtherStaffBot"
    client = StaffClient(staff_id=selected_id, bot_username=selected_bot)
    assert await verify_staff(client, staff_id=selected_id, staff_bot=selected_bot)
    assert client.calls == [selected_id, selected_bot]


async def test_fresh_session_selects_new_staff_instead_of_old_staff():
    selected_id = -1004430959898
    old_staff = SimpleNamespace(id=4300060813, title="Crimson Staff")
    selected_staff = SimpleNamespace(id=4430959898, title="Crimson Staff")
    client = FreshStaffClient(
        [
            SimpleNamespace(id=STAFF_CHAT_ID, entity=old_staff),
            SimpleNamespace(id=selected_id, entity=selected_staff),
        ],
        staff_id=selected_id,
    )
    assert await verify_staff(client, staff_id=selected_id)
    assert client.seen_staff is selected_staff
    assert client.calls == [selected_id, STAFF_BOT]


async def test_old_staff_membership_cannot_satisfy_new_staff_check():
    client = FreshStaffClient(
        [SimpleNamespace(id=STAFF_CHAT_ID, entity=SimpleNamespace(id=4300060813))],
        staff_id=-1004430959898,
    )
    with pytest.raises(ValueError, match="not in the account's dialogs"):
        await verify_staff(client, staff_id=-1004430959898)
    assert client.seen_staff is None


async def test_selected_staff_bot_identity_must_match():
    with pytest.raises(ValueError, match="Unexpected staff bot identity"):
        await verify_staff(StaffClient(), staff_bot="OtherStaffBot")


@pytest.mark.parametrize("value", ["0", "42", "-0", "group", str(-(2**63) - 1)])
def test_staff_id_cli_rejects_invalid_chat_ids(value):
    with pytest.raises(argparse.ArgumentTypeError):
        module.staff_chat_id(value)


def test_staff_cli_accepts_negative_id_and_optional_username_prefix():
    assert module.staff_chat_id("-1004430959898") == -1004430959898
    assert module.staff_bot_username("@ghStaffBot") == "ghStaffBot"


@pytest.mark.parametrize("value", ["", "https://t.me/ghStaffBot", "12345", "with spaces"])
def test_staff_bot_cli_rejects_invalid_usernames(value):
    with pytest.raises(argparse.ArgumentTypeError):
        module.staff_bot_username(value)


@pytest.mark.parametrize(
    ("arguments", "expected_id", "expected_bot"),
    [
        ([], STAFF_CHAT_ID, STAFF_BOT),
        (
            ["--staff-id", "-1004430959898", "--staff-bot", "@OtherStaffBot"],
            -1004430959898,
            "OtherStaffBot",
        ),
    ],
)
def test_cli_passes_selected_staff_to_login(
    tmp_path, monkeypatch, arguments, expected_id, expected_bot
):
    seen = {}

    async def fake_login(state_dir, *, staff_id, staff_bot):
        seen.update(state_dir=state_dir, staff_id=staff_id, staff_bot=staff_bot)

    monkeypatch.setattr(module, "login", fake_login)
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.sys, "argv", ["login", "--state-dir", str(tmp_path), *arguments])
    module.main()
    assert seen == {"state_dir": tmp_path, "staff_id": expected_id, "staff_bot": expected_bot}

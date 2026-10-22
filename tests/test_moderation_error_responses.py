"""Real routing/SQL regressions for honest, command-bound private diagnostics."""

from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import GetChatMember, GetMe, SendMessage
from test_group_moderation_foundation import CHAT
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import known, no_sanctions
from test_moderation_usernames import username_journey as username_journey

from app.bot.callbacks import LocalAction
from app.bot.moderation_errors import COMMANDS, basic_kind, command_name, error_message
from app.errors import DomainError
from app.i18n import t, use_language
from app.services import Service
from app.telegram_failures import IdentityLookup

pytestmark = pytest.mark.asyncio


class Text(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.feed(html)

    def handle_data(self, data):
        self.parts.append(data)

    def __str__(self):
        return "".join(self.parts)


def messages(journey, chat=42):
    return [m for m in journey.transport.calls if isinstance(m, SendMessage) and m.chat_id == chat]


def assert_private(journey, command, kind, *, private_chat=False):
    sent = messages(journey, 42 if private_chat else CHAT)
    assert len(sent) == 1 and sent[0].parse_mode == "HTML"
    assert sent[0].text.startswith("<b><u>" + t("lm.diagnostic." + kind + ".title") + "</u></b>")
    assert command in str(Text(sent[0].text)) and "{command}" not in sent[0].text
    if private_chat:
        assert sent[0].ephemeral_message_parameters is None
        assert not messages(journey, CHAT)
    else:
        assert sent[0].model_dump(exclude_none=True)["ephemeral_message_parameters"] == {
            "receiver_user_id": 42
        }
        assert not messages(journey, 42)
    assert "Only visible to you" not in sent[0].text
    return sent[0]


@pytest.mark.parametrize("command", sorted(COMMANDS))
@pytest.mark.parametrize("argument", ["", "0", "@bad", "@<b>bad</b>"])
async def test_invalid_or_missing_target_is_private_for_every_command(
    username_journey, database, command, argument
):
    journey, native = username_journey
    await journey.send(f"/{command} {argument}", actor=42, chat=CHAT)
    result = assert_private(journey, "/" + command, "target_missing")
    assert "@<b>bad</b>" not in result.text
    assert native.calls == []
    await no_sanctions(database)


@pytest.mark.parametrize("command", sorted(COMMANDS))
async def test_unverified_username_is_not_misreported_as_rights_or_absence(
    username_journey, database, command
):
    journey, native = username_journey
    await journey.send(f"/{command} @unknown_user", actor=42, chat=CHAT)
    result = assert_private(journey, "/" + command, "identity_unverified")
    assert t("lm.diagnostic.actor_rights.title") not in result.text
    assert t("lm.diagnostic.not_in_group.title") not in result.text
    assert native.calls == []
    await no_sanctions(database)


def fail_target_then_return(native, member):
    original = native.get_chat_member
    attempts = []

    async def get(chat_id, user_id):
        if user_id == 51:
            attempts.append(1)
            if len(attempts) == 1:
                raise TelegramBadRequest(
                    method=GetChatMember(chat_id=chat_id, user_id=user_id),
                    message="USER_NOT_PARTICIPANT",
                )
            return member
        return await original(chat_id, user_id)

    native.get_chat_member = get
    return attempts


@pytest.mark.parametrize("command", sorted(COMMANDS))
async def test_absence_requires_fresh_matching_id_username_and_left_status(
    username_journey, database, command
):
    journey, native = username_journey
    await known(database, native)
    absent = native.peer(51, "left")
    absent.user.username = "target_user"
    attempts = fail_target_then_return(native, absent)
    await journey.send(f"/{command} @target_user", actor=42, chat=CHAT)
    result = assert_private(journey, "/" + command, "not_in_group")
    assert "Veiksmas neatliktas." in str(Text(result.text)) and len(attempts) == 2
    assert native.calls == []
    await no_sanctions(database)


@pytest.mark.parametrize(
    "change",
    ["wrong_id", "wrong_username", "unknown_status", "restricted_member", "unverified_restricted"],
)
async def test_incomplete_member_proof_never_claims_absence(username_journey, database, change):
    journey, native = username_journey
    await known(database, native)
    member = native.peer(52 if change == "wrong_id" else 51, "left")
    member.user.username = "other_user" if change == "wrong_username" else "target_user"
    if change == "unknown_status":
        member.status = "unknown"
    if change in {"restricted_member", "unverified_restricted"}:
        member.status = "restricted"
        if change == "restricted_member":
            member.is_member = True
    fail_target_then_return(native, member)
    await journey.send("/warn @target_user", actor=42, chat=CHAT)
    result = assert_private(journey, "/warn", "identity_unverified")
    assert t("lm.diagnostic.not_in_group.title") not in result.text
    await no_sanctions(database)


@pytest.mark.parametrize("status", ["kicked", "restricted"])
async def test_verified_nonmember_statuses_are_recognized(username_journey, database, status):
    journey, native = username_journey
    await known(database, native)
    member = native.peer(51, status)
    member.user.username = "target_user"
    if status == "restricted":
        member.is_member = False
    fail_target_then_return(native, member)
    await journey.send("/warn @target_user", actor=42, chat=CHAT)
    assert_private(journey, "/warn", "not_in_group")
    await no_sanctions(database)


@pytest.mark.parametrize("command", sorted(COMMANDS))
async def test_admin_permission_loss_is_not_a_user_lookup_error(
    username_journey, database, command
):
    journey, native = username_journey
    native.members[CHAT, 42] = native.peer(42)
    await journey.send(f"/{command} @unknown_user", actor=42, chat=CHAT)
    result = assert_private(journey, "/" + command, "actor_rights")
    assert t("lm.diagnostic.target_missing.title") not in result.text
    assert t("lm.diagnostic.identity_unverified.title") not in result.text
    assert native.calls == []
    await no_sanctions(database)


@pytest.mark.parametrize("command", sorted(COMMANDS))
async def test_revoked_bot_rights_cannot_be_misreported_as_absent_target(
    username_journey, database, command
):
    journey, native = username_journey
    await known(database, native)
    absent = native.peer(51, "left")
    absent.user.username = "target_user"
    attempts = fail_target_then_return(native, absent)
    native.members[CHAT, native.id].can_restrict_members = False
    await journey.send(f"/{command} @target_user", actor=42, chat=CHAT)
    result = assert_private(journey, "/" + command, "bot_rights")
    assert len(attempts) == 0  # Bot rights are now checked before any target lookup.
    assert t("lm.diagnostic.not_in_group.title") not in result.text
    await no_sanctions(database)


@pytest.mark.parametrize("command", sorted(COMMANDS - {"warnings", "warns"}))
async def test_invalid_numeric_peer_is_not_a_permissions_error(username_journey, database, command):
    journey, native = username_journey
    original = native.get_chat_member

    async def get(chat_id, user_id):
        if user_id == 51:
            raise TelegramBadRequest(
                method=GetChatMember(chat_id=chat_id, user_id=user_id), message="USER_ID_INVALID"
            )
        return await original(chat_id, user_id)

    native.get_chat_member = get
    await journey.send(f"/{command} 51", actor=42, chat=CHAT)
    assert_private(journey, "/" + command, "target_missing")
    await no_sanctions(database)


@pytest.mark.parametrize("code", ["not_found", "invalid_username", "not_user"])
async def test_authoritative_mtproto_non_user_is_missing_not_unverified(
    username_journey, database, settings, monkeypatch, code
):
    journey, native = username_journey

    async def lookup(*args, **kwargs):
        return IdentityLookup(code=code)

    monkeypatch.setattr(settings, "moderation_mtproto_enabled", True)
    monkeypatch.setattr(
        "app.mtproto_relay.active_relay", lambda: SimpleNamespace(lookup_identity=lookup)
    )
    await journey.send("/warn @unknown_user", actor=42, chat=CHAT)
    assert_private(journey, "/warn", "target_missing")
    await no_sanctions(database)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("command", sorted(COMMANDS))
async def test_translated_html_dm_mentions_actual_bot_addressed_command(
    username_journey, database, settings, lang, command
):
    journey, _ = username_journey
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    called = f"/{command}@redsafecheckbot"
    await journey.send(called, actor=42, chat=CHAT)
    with use_language(lang):
        result = assert_private(journey, called, "target_missing")
    assert "<b><u>" in result.text
    assert "<script>" not in result.text
    await no_sanctions(database)


@pytest.mark.parametrize("failure", ["timeout", "rate_limit", "network", "server"])
async def test_unknown_ephemeral_delivery_never_resends_or_publishes(username_journey, failure):
    journey, _ = username_journey
    original = journey.transport.make_request
    attempts = []

    async def request(bot, method, timeout=None):
        if isinstance(method, SendMessage):
            attempts.append(method)
            assert method.chat_id == CHAT
            assert method.ephemeral_message_parameters.receiver_user_id == 42
            if failure == "timeout":
                raise TimeoutError()
            if failure == "network":
                raise TelegramNetworkError(method=method, message="network unavailable")
            if failure == "server":
                raise TelegramServerError(method=method, message="server unavailable")
            raise TelegramRetryAfter(method=method, message="FloodWait", retry_after=30)
        return await original(bot, method, timeout)

    journey.transport.make_request = request
    await journey.send("/warn @unknown_user", actor=42, chat=CHAT)
    assert len(attempts) == 1
    assert not any(isinstance(call, GetMe) for call in journey.transport.calls)


@pytest.mark.parametrize("dm_blocked", [False, True])
async def test_definite_ephemeral_rejection_falls_back_privately(username_journey, dm_blocked):
    journey, _ = username_journey
    original = journey.transport.make_request
    attempts = []

    async def request(bot, method, timeout=None):
        if isinstance(method, SendMessage):
            attempts.append(method)
            if method.ephemeral_message_parameters is not None:
                raise TelegramBadRequest(method=method, message="ephemeral unavailable")
            if method.chat_id == 42 and dm_blocked:
                raise TelegramForbiddenError(method=method, message="bot blocked")
        return await original(bot, method, timeout)

    journey.transport.make_request = request
    await journey.send("/warn @unknown_user", actor=42, chat=CHAT)
    assert attempts[0].ephemeral_message_parameters.receiver_user_id == 42
    assert attempts[1].chat_id == 42 and attempts[1].parse_mode == "HTML"
    if not dm_blocked:
        assert len(attempts) == 2
        assert t("lm.diagnostic.identity_unverified.title") in attempts[1].text
    else:
        assert len(attempts) == 3
        public = attempts[2]
        assert public.chat_id == CHAT and public.ephemeral_message_parameters is None
        assert "/warn" in public.text and "unknown_user" not in public.text
        assert t("lm.diagnostic.identity_unverified.title") not in public.text
        assert "Only visible" not in public.text
        assert public.reply_markup.inline_keyboard[0][0].url == "https://t.me/redsafecheckbot"


async def test_private_fsm_error_uses_selected_command_without_replaying_it(
    username_journey, database
):
    journey, native = username_journey
    await journey.click(LocalAction(action="action", chat=CHAT, value="KICK").pack(), actor=42)
    journey.transport.calls.clear()
    await journey.send("@unknown_user", actor=42)
    assert_private(journey, "/kick", "identity_unverified", private_chat=True)
    assert native.calls == []
    await no_sanctions(database)


async def test_numeric_warning_history_for_departed_user_is_not_newly_blocked(username_journey):
    journey, native = username_journey
    native.members[CHAT, 51] = native.peer(51, "left")
    await journey.send("/warns 51", actor=42, chat=CHAT)
    assert t("lm.no_history") in journey.text()
    assert t("lm.diagnostic.not_in_group.title") not in journey.text()
    assert native.calls == []


async def test_existing_preemptive_ban_for_departed_target_is_unchanged(username_journey):
    journey, native = username_journey
    native.members[CHAT, 51] = native.peer(51, "left")
    await journey.send("/ban 51 30m", actor=42, chat=CHAT)
    assert native.calls[0][0] == "ban" and native.calls[0][1]["user_id"] == 51
    assert t("lm.diagnostic.not_in_group.title") not in journey.text()


async def test_duration_error_is_not_false_user_not_found(username_journey, database):
    journey, _ = username_journey
    await journey.send("/ban 51 2h", actor=42, chat=CHAT)
    assert_private(journey, "/ban", "input_invalid")
    await no_sanctions(database)


async def test_exact_lt_texts_and_safe_command_interpolation():
    expected = {
        "target_missing": "Naudotojas nerastas\n\nNorėdami naudoti komandą /warn, nurodykite naudotojo vardą (@username), Telegram ID arba paminėkite naudotoją.\n\nTaip pat galite atsakyti į jo žinutę.",
        "not_in_group": "Naudotojas nėra grupės narys\n\nNurodytas naudotojas nepriklauso šiai grupei.\n\nVeiksmas neatliktas.\n\nKomanda: /warn",
        "identity_unverified": "Nepavyko patvirtinti naudotojo\n\nNepavyko saugiai susieti @username su Telegram ID.\n\nNaudokite Telegram ID arba atsakykite į naudotojo žinutę.\n\nKomanda: /warn",
    }
    with use_language("lt"):
        for kind, plain in expected.items():
            value = error_message("/warn", kind, fallback="")
            assert str(Text(value)) == plain
            assert value.startswith("<b><u>") and "</u></b>" in value
        malicious = error_message("/warn<script>alert(1)</script>", "target_missing", fallback="")
        assert "<script>" not in malicious and "alert(1)" not in malicious
    assert command_name("BAN") == "/ban"
    assert basic_kind(DomainError("group_moderation_forbidden")) == "actor_rights"


@pytest.mark.parametrize("actor", [42, 987654321])
async def test_native_recipient_and_topic_are_preserved_without_dm(actor):
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, Message

    from app.bot.moderation_errors import deliver_error

    bot = SimpleNamespace(send_message=AsyncMock(return_value=None))
    message = Message(
        message_id=5,
        date=0,
        chat=Chat(id=CHAT, type="supergroup"),
        message_thread_id=37,
    ).as_(bot)
    await deliver_error(message, actor, "<b><u>Test</u></b>", "/warn")
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.call_args
    assert args == (CHAT, "<b><u>Test</u></b>")
    assert kwargs["parse_mode"] == "HTML" and kwargs["message_thread_id"] == 37
    assert kwargs["ephemeral_message_parameters"].model_dump(exclude_none=True) == {
        "receiver_user_id": actor
    }


@pytest.mark.parametrize("command", ["ban", "mute"])
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_legacy_permanent_flags_do_not_block_an_enabled_command(
    username_journey, database, settings, command, lang
):
    from sqlalchemy import update

    from app.models import GroupModerationSettings

    journey, native = username_journey
    async with database() as session:
        await session.execute(
            update(GroupModerationSettings)
            .where(GroupModerationSettings.chat_id == CHAT)
            .values(**{"allow_permanent_" + command + "s": False})
        )
        await session.commit()
    with use_language(lang):
        # Explicit fixture language instead of relying on a process context across middleware.
        async with database() as session:
            await Service(settings, session).set_language(42, lang)
        await journey.send("/" + command + " 51", actor=42, chat=CHAT)
        result = messages(journey, CHAT)[-1]
        assert result.parse_mode == "HTML"
        assert t("lm.notice.permanent") in result.text
        assert t("lm.diagnostic.module_disabled.title") not in result.text
        assert result.reply_markup is not None
    assert len(native.calls) == 1 and native.calls[0][1].get("until_date") is None


@pytest.mark.parametrize("command", ["ban", "mute"])
async def test_authorized_command_initializes_its_capability_without_a_module_toggle(
    username_journey, database, command
):
    from sqlalchemy import update

    from app.models import GroupModerationSettings

    journey, native = username_journey
    async with database() as session:
        await session.execute(
            update(GroupModerationSettings)
            .where(GroupModerationSettings.chat_id == CHAT)
            .values(**{command + "s_enabled": False})
        )
        await session.commit()
    await journey.send("/" + command + " 51 30m", actor=42, chat=CHAT)
    sent = messages(journey, CHAT)
    assert len(sent) == 1 and sent[0].ephemeral_message_parameters is None
    assert len(native.calls) == 1
    async with database() as session:
        policy = await session.get(GroupModerationSettings, CHAT)
        assert getattr(policy, command + "s_enabled") is True
        assert policy.max_duration_seconds == 2592000

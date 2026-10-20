"""Username inputs must become live-verified numeric targets before any write."""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select, update
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey

from app.bot.callbacks import LocalAction
from app.bot.local_admin import parse_target_input
from app.errors import DomainError
from app.i18n import t, use_language
from app.models import GroupModerationDraft, GroupSanction, ManagedGroup, ScamRecord, User, now
from app.moderation_services import GroupModerationService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def username_journey(local_journey):
    journey, native = local_journey
    original = journey.transport.make_request

    async def request(bot, method, timeout=None):
        result = await original(bot, method, timeout)
        # Real BaseSession.check_response supplies bot context to returned models.
        return result.as_(bot) if isinstance(result, Message) else result

    journey.transport.make_request = request
    yield journey, native


async def known(factory, native, *, user_id=51, username="target_user"):
    async with factory() as session:
        session.add(User(telegram_id=user_id, username=username))
        await session.commit()
    native.members[CHAT, user_id] = native.peer(user_id)
    native.members[CHAT, user_id].user.username = username


async def no_sanctions(factory):
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0
        assert await session.scalar(select(func.count()).select_from(GroupModerationDraft)) == 0


@pytest.mark.parametrize("kind", ["BAN", "UNBAN", "MUTE", "UNMUTE", "WARN", "KICK", "WARNINGS"])
async def test_username_commands_pin_numeric_target(username_journey, database, kind):
    journey, native = username_journey
    await known(database, native)
    if kind in {"UNBAN", "UNMUTE"}:
        await journey.send(
            "/ban 51 30m" if kind == "UNBAN" else "/mute 51 30m", actor=42, chat=CHAT
        )
        native.members[CHAT, 51].user.username = "target_user"
        native.calls.clear()
    duration = " 30m" if kind in {"BAN", "MUTE"} else ""
    await journey.send(f"/{kind.lower()} @TaRgEt_UsEr{duration}", actor=42, chat=CHAT)
    async with database() as session:
        if kind == "WARNINGS":
            assert "51" in journey.text()
            assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0
        else:
            draft = await session.scalar(
                select(GroupModerationDraft).where(GroupModerationDraft.kind == kind)
            )
            assert draft is not None and draft.telegram_id == 51
            if kind == "WARN":
                row = await session.scalar(select(GroupSanction))
                assert row.telegram_id == 51 and row.status == "RECORDED"
            else:
                assert native.calls and native.calls[0][1]["user_id"] == 51
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0


async def test_username_private_menu_creates_confirmable_numeric_draft(username_journey, database):
    journey, native = username_journey
    await known(database, native)
    await journey.click(LocalAction(action="action", chat=CHAT, value="BAN").pack(), actor=42)
    await journey.send("@target_user 1h <b>reason</b>", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        assert draft is not None and draft.telegram_id == 51 and draft.kind == "BAN"
        assert draft.reason == "<b>reason</b>" and draft.status == "PENDING"
    assert native.calls == []  # Menu input never bypasses explicit confirmation.


@pytest.mark.parametrize("kind", ["BAN", "MUTE", "WARN", "KICK", "UNBAN", "UNMUTE"])
async def test_unknown_username_command_has_no_write_or_telegram_action(
    username_journey, database, kind
):
    journey, native = username_journey
    await journey.send(
        f"/{kind.lower()} @unknown_user 30m"
        if kind in {"BAN", "MUTE"}
        else f"/{kind.lower()} @unknown_user",
        actor=42,
        chat=CHAT,
    )
    assert native.calls == []
    assert t("lm.username_unknown") in journey.text()
    await no_sanctions(database)
    async with database() as session:
        assert await session.scalar(select(User).where(User.username == "unknown_user")) is None


async def failure_contract(factory, native):
    await enabled(factory, native)
    await known(factory, native)
    async with factory() as session:
        local = GroupModerationService(session, native)
        assert await local.resolve_username(CHAT, 42, "@TARGET_USER") == 51
    native.members[CHAT, 51].user.username = "different_user"
    async with factory() as session:
        with pytest.raises(DomainError, match="group_moderation_username_changed"):
            await GroupModerationService(session, native).resolve_username(CHAT, 42, "@target_user")
    native.members[CHAT, 51].user.username = "target_user"
    async with factory() as session:
        session.add(User(telegram_id=52, username="TARGET_USER"))
        await session.commit()
        with pytest.raises(DomainError, match="group_moderation_username_unknown"):
            await GroupModerationService(session, native).resolve_username(CHAT, 42, "@target_user")
    await no_sanctions(factory)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 2
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0


async def test_cached_stale_and_ambiguous_usernames_fail_closed(database):
    await failure_contract(database, NativeBot())


async def test_postgres_username_identity_contract(postgres_contract):
    factory, _ = postgres_contract
    await failure_contract(factory, NativeBot())


@pytest.mark.parametrize(
    "value",
    ["@a", "@a.bcd", "@<b>user</b>", "@tаrget_user", "@@target_user", "@user;DROP", "x" * 100],
)
async def test_malformed_username_is_rejected_before_lookup(database, value):
    async with database() as session:
        with pytest.raises(DomainError, match="group_moderation_invalid_username"):
            await GroupModerationService(session, NativeBot()).resolve_username(CHAT, 42, value)
    await no_sanctions(database)


@pytest.mark.parametrize("username_only", [False, True])
async def test_unknown_or_username_only_profile_does_not_become_numeric(database, username_only):
    native = NativeBot()
    await enabled(database, native)
    if username_only:
        async with database() as session:
            session.add(User(username="target_user"))
            await session.commit()
    async with database() as session:
        with pytest.raises(DomainError, match="group_moderation_username_unknown"):
            await GroupModerationService(session, native).resolve_username(CHAT, 42, "@target_user")
    await no_sanctions(database)


@pytest.mark.parametrize("change", ["missing_name", "wrong_numeric_id", "api_failure", "timeout"])
async def test_unverified_live_identity_cannot_use_cached_name(database, monkeypatch, change):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native)
    original = native.get_chat_member

    async def member(chat, user):
        if user == 51:
            if change == "missing_name":
                return native.peer(51)
            if change == "wrong_numeric_id":
                return SimpleNamespace(user=SimpleNamespace(id=52, username="target_user"))
            if change == "api_failure":
                raise OSError("transport failed")
            await asyncio.sleep(1)
        return await original(chat, user)

    native.get_chat_member = member
    monkeypatch.setattr("app.group_authorization.RIGHTS_TIMEOUT", 0.01)
    async with database() as session:
        with pytest.raises((DomainError, OSError, TimeoutError)):
            await GroupModerationService(session, native).resolve_username(CHAT, 42, "@target_user")
    await no_sanctions(database)


@pytest.mark.parametrize("change", ["actor", "bot"])
async def test_rights_revoked_after_username_lookup_deny_prepare(
    username_journey, database, change
):
    journey, native = username_journey
    await known(database, native)
    original = native.get_chat_member

    async def member(chat, user):
        value = await original(chat, user)
        if user == 51:
            revoked = 42 if change == "actor" else native.id
            native.members[CHAT, revoked] = native.peer(revoked)
        return value

    native.get_chat_member = member
    await journey.send("/warn @target_user reason", actor=42, chat=CHAT)
    assert native.calls == []
    await no_sanctions(database)


async def test_disabled_group_and_global_only_admin_cannot_lookup(database):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native)
    async with database() as session:
        local = GroupModerationService(session, native)
        with pytest.raises(DomainError, match="group_moderation_forbidden"):
            await local.resolve_username(CHAT, 900, "@target_user")
        await session.execute(
            update(ManagedGroup).where(ManagedGroup.chat_id == CHAT).values(enabled=False)
        )
        await session.commit()
        with pytest.raises(DomainError, match="group_moderation_group_unavailable"):
            await local.resolve_username(CHAT, 42, "@target_user")
    await no_sanctions(database)


@pytest.mark.parametrize("target", [42, 123456])
async def test_username_does_not_bypass_protected_admin_and_bot(username_journey, database, target):
    journey, native = username_journey
    async with database() as session:
        session.add(User(telegram_id=target, username="protected_user"))
        await session.commit()
    native.members[CHAT, target].user.username = "protected_user"
    native.admin(CHAT, 43)
    await journey.send("/warn @protected_user reason", actor=43, chat=CHAT)
    assert t("lm.error") in journey.text()
    assert native.calls == []
    await no_sanctions(database)


async def test_explicit_username_never_falls_back_to_reply(database):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native)
    reply = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=TelegramUser(id=52, is_bot=False, first_name="Other"),
    )
    async with database() as session:
        local = GroupModerationService(session, native)
        assert (await parse_target_input(local, CHAT, 42, "@target_user reason", reply, "WARN"))[
            0
        ] == 51
        with pytest.raises(DomainError):
            await parse_target_input(local, CHAT, 42, "@missing_user reason", reply, "WARN")
    await no_sanctions(database)


async def test_username_reassignment_does_not_retarget_pending_confirmation(database):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native)
    async with database() as session:
        local = GroupModerationService(session, native)
        target, until, reason = await parse_target_input(
            local, CHAT, 42, "@target_user reason", None, "WARN"
        )
        draft = await local.prepare(
            CHAT, 42, target, "WARN", until_date=until, reason=reason, request="username-pinned-001"
        )
        await session.execute(
            update(User).where(User.telegram_id == 51).values(username="renamed_user")
        )
        session.add(User(telegram_id=52, username="target_user"))
        await session.commit()
        native.members[CHAT, 51].user.username = "renamed_user"
        native.members[CHAT, 52] = native.peer(52)
        native.members[CHAT, 52].user.username = "target_user"
        result = await local.confirm(draft.id, 42)
        assert result.telegram_id == 51 and result.status == "RECORDED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(GroupSanction)
                .where(GroupSanction.telegram_id == 52)
            )
            == 0
        )


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_username_instructions_and_errors_are_localized(lang):
    with use_language(lang):
        for key in ("lm.target", "lm.invalid", "lm.username_unknown", "lm.username_changed"):
            assert "@username" in t(key) and t(key) != key

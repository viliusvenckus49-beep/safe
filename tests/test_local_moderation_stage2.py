"""Stage-2 commands use real routing/SQL and stateful native Telegram API doubles."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    BanChatMember,
    EditMessageText,
    GetChat,
    GetChatMember,
    RestrictChatMember,
    SendMessage,
    UnbanChatMember,
)
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select, update
from test_group_moderation_foundation import CHAT, NativeBot, setup_groups
from test_integration_contracts import postgres_contract as postgres_contract
from test_telegram import Journey, Transport

from app.bot.callbacks import LocalAction
from app.bot.handlers import create_router
from app.bot.local_admin import parse_target, receipt_text
from app.bot.local_moderation import LocalModerationExecutor
from app.errors import DomainError
from app.i18n import CATALOGS, t, use_language
from app.models import (
    GroupModerationDraft,
    GroupSanction,
    GroupSanctionJob,
    ReputationEvent,
    ScamRecord,
    now,
)
from app.moderation_services import GroupModerationService
from app.services import Service

pytestmark = pytest.mark.asyncio


async def enabled(sessions, bot):
    await setup_groups(sessions, bot)
    async with sessions() as session:
        await GroupModerationService(session, bot).configure(
            CHAT,
            42,
            enabled=True,
            bans_enabled=True,
            mutes_enabled=True,
            warnings_enabled=True,
            kicks_enabled=True,
            allow_permanent_bans=True,
            allow_permanent_mutes=True,
        )


async def prepared(
    sessions, bot, kind="BAN", *, key="stage-two-001", until=None, actor=42, reason=""
):
    async with sessions() as session:
        return await GroupModerationService(session, bot).prepare(
            CHAT, actor, 51, kind, until_date=until, reason=reason, request=key
        )


async def confirmed(sessions, bot, draft, actor=42):
    async with sessions() as session:
        return await GroupModerationService(session, bot).confirm(draft.id, actor)


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize("temporary", [False, True])
async def test_restrictions_and_release_are_native_and_keep_scam_rep_separate(
    database, kind, temporary
):
    bot = NativeBot()
    await enabled(database, bot)
    until = (now() + timedelta(hours=1)).replace(microsecond=0) if temporary else None
    draft = await prepared(database, bot, kind, until=until)
    row = await confirmed(database, bot, draft)
    worker = LocalModerationExecutor(database, bot)
    await worker.process(limit=2, sanction_id=row.id)
    assert bot.calls[0][1]["until_date"] == until
    revoke = await prepared(
        database, bot, "UNBAN" if kind == "BAN" else "UNMUTE", key="stage-two-002"
    )
    await confirmed(database, bot, revoke)
    await worker.process(limit=2, sanction_id=row.id)
    async with database() as session:
        assert (await session.get(GroupSanction, row.id)).status == "REVOKED"
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(ReputationEvent)) == 0
    assert len(bot.calls) == 2


async def test_kick_is_two_durable_confirmed_steps(database):
    bot = NativeBot()
    await enabled(database, bot)
    draft = await prepared(database, bot, "KICK")
    row = await confirmed(database, bot, draft)
    await LocalModerationExecutor(database, bot).process(limit=1, sanction_id=row.id)
    assert [c[0] for c in bot.calls] == ["ban"]
    await LocalModerationExecutor(database, bot).process(limit=1, sanction_id=row.id)
    assert [c[0] for c in bot.calls] == ["ban", "unban"]
    assert bot.calls[1][1]["only_if_banned"] is True
    async with database() as session:
        assert (await session.get(GroupSanction, row.id)).status == "COMPLETED"


async def test_kick_never_unbans_new_scam_identity(database, settings):
    bot = NativeBot()
    await enabled(database, bot)
    row = await confirmed(database, bot, await prepared(database, bot, "KICK"))
    await LocalModerationExecutor(database, bot).process(sanction_id=row.id)
    async with database() as session:
        await Service(settings, session).add_scam(900, "51")
    await LocalModerationExecutor(database, bot).process(sanction_id=row.id)
    assert len(bot.calls) == 1
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        text, _ = await receipt_text(session, draft)
        assert t("lm.protected") in text and t("lm.done") not in text


async def test_warning_is_persistent_and_confirmation_idempotent(database):
    bot = NativeBot()
    await enabled(database, bot)
    draft = await prepared(database, bot, "WARN")
    first = await confirmed(database, bot, draft)
    second = await confirmed(database, bot, draft)
    assert first.id == second.id and first.status == "RECORDED"
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 1
        assert await session.scalar(select(func.count()).select_from(GroupSanctionJob)) == 0
    assert bot.calls == []


async def test_foreign_admin_cannot_confirm_or_cancel_someone_elses_draft(database):
    bot = NativeBot()
    bot.admin(CHAT, 43)
    await enabled(database, bot)
    draft = await prepared(database, bot)
    for action in ("confirm", "cancel_draft"):
        async with database() as session:
            with pytest.raises(DomainError, match="invalid_draft"):
                await getattr(GroupModerationService(session, bot), action)(draft.id, 43)
    bot.members[CHAT, 42] = bot.peer(42)
    with pytest.raises(DomainError, match="forbidden"):
        await confirmed(database, bot, draft)
    assert bot.calls == []


async def confirmation_race(sessions):
    bot = NativeBot()
    await enabled(sessions, bot)
    draft = await prepared(sessions, bot)

    async def confirm():
        return await confirmed(sessions, bot, draft)

    async def cancel():
        async with sessions() as session:
            return await GroupModerationService(session, bot).cancel_draft(draft.id, 42)

    await asyncio.gather(confirm(), cancel(), return_exceptions=True)
    async with sessions() as session:
        stored = await session.get(GroupModerationDraft, draft.id)
        count = await session.scalar(select(func.count()).select_from(GroupSanction))
        assert (stored.status, count) in {("CONFIRMED", 1), ("CANCELLED", 0)}
    await LocalModerationExecutor(sessions, bot).process(limit=2)
    assert len(bot.calls) <= 1


async def test_sqlite_confirmation_cancel_race(database):
    await confirmation_race(database)


async def test_postgres_confirmation_cancel_race(postgres_contract):
    sessions, _ = postgres_contract
    await confirmation_race(sessions)


async def test_expired_draft_never_executes(database):
    bot = NativeBot()
    await enabled(database, bot)
    draft = await prepared(database, bot)
    async with database() as session:
        await session.execute(
            update(GroupModerationDraft).values(expires_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    with pytest.raises(DomainError, match="invalid_draft"):
        await confirmed(database, bot, draft)
    assert not bot.calls


async def test_explicit_new_unban_retries_definite_failure_but_old_click_does_not(database):
    bot = NativeBot()
    await enabled(database, bot)
    row = await confirmed(database, bot, await prepared(database, bot))
    worker = LocalModerationExecutor(database, bot)
    await worker.process(sanction_id=row.id)
    first = await prepared(database, bot, "UNBAN", key="first-unban")
    await confirmed(database, bot, first)
    bot.api_false = True
    await worker.process(sanction_id=row.id)
    assert len(bot.calls) == 2
    bot.api_false = False
    await confirmed(database, bot, first)
    await worker.process(sanction_id=row.id)
    assert len(bot.calls) == 2
    second = await prepared(database, bot, "UNBAN", key="second-unban")
    await confirmed(database, bot, second)
    await worker.process(sanction_id=row.id)
    await confirmed(database, bot, first)
    assert len(bot.calls) == 3
    async with database() as session:
        assert (await session.get(GroupSanction, row.id)).status == "REVOKED"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("51 30m", 1800),
        ("51 1h reason", 3600),
        ("51 1d", 86400),
        ("51 7d", 604800),
        ("51 30d", 2592000),
        ("51 neribotai", None),
        ("51 навсегда", None),
        ("51", None),
    ],
)
async def test_duration_parser(text, expected):
    target, until, _ = parse_target(text, None, "BAN")
    assert target == 51
    assert (
        until is None if expected is None else abs((until - now()).total_seconds() - expected) < 2
    )


@pytest.mark.parametrize(
    "text", ["@someone 1h", "0", "-51", "51 2h", "51 0m", "５１", "9223372036854775808"]
)
async def test_parser_refuses_usernames_and_invalid_durations(text):
    with pytest.raises(DomainError):
        parse_target(text, None, "BAN")


class LocalTransport(Transport):
    def __init__(self, native):
        super().__init__()
        self.native = native

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, GetChatMember):
            return await self.native.get_chat_member(method.chat_id, method.user_id)
        if isinstance(method, GetChat):
            return await self.native.get_chat(method.chat_id)
        if isinstance(method, (BanChatMember, UnbanChatMember, RestrictChatMember)):
            target = {
                BanChatMember: "ban_chat_member",
                UnbanChatMember: "unban_chat_member",
                RestrictChatMember: "restrict_chat_member",
            }[type(method)]
            values = method.model_dump(exclude_none=True)
            if isinstance(method, RestrictChatMember):
                values["permissions"] = method.permissions
            return await getattr(self.native, target)(**values)
        return await super().make_request(bot, method, timeout)


@pytest_asyncio.fixture
async def local_journey(database, settings):
    native = NativeBot()
    await enabled(database, native)
    transport = LocalTransport(native)
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    yield Journey(bot, dispatcher, transport), native
    await dispatcher.storage.close()
    await bot.session.close()


async def test_group_raw_ban_then_unban_and_no_reason_line(local_journey):
    journey, native = local_journey
    await journey.send("/ban 51 30m", actor=42, chat=CHAT)
    assert [c[0] for c in native.calls] == ["ban"]
    assert t("lm.done") in journey.text() and "Priežastis:" not in journey.text()
    await journey.send("/unban 51", actor=42, chat=CHAT)
    assert [c[0] for c in native.calls] == ["ban", "unban"]


async def test_reply_command_targets_actual_sender(local_journey):
    journey, native = local_journey
    reply = Message(
        message_id=88,
        date=datetime.now(UTC),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=51, is_bot=False, first_name="Target"),
        text="Hello",
    )
    await journey.send("/mute 1h", actor=42, chat=CHAT, reply_to_message=reply)
    assert native.calls[0][1]["user_id"] == 51


async def test_ordinary_and_global_admin_do_not_gain_local_commands(local_journey):
    journey, native = local_journey
    for actor in (1, 900):
        await journey.send("/ban 51", actor=actor, chat=CHAT)
        await journey.send("/moderation", actor=actor)
    assert native.calls == []
    assert t("lm.empty") in journey.text()
    assert not any(
        getattr(call, "reply_markup", None)
        and any(
            "lm2|action" in (b.callback_data or "")
            for row in call.reply_markup.inline_keyboard
            for b in row
        )
        for call in journey.transport.calls
    )


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_private_ui_confirmation_localization_history_and_no_callback_leak(
    local_journey, database, settings, lang
):
    journey, native = local_journey
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="group", chat=CHAT).pack(), actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="WARN").pack(), actor=42)
    await journey.send("51 <reason>", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        draft_id = draft.id
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=1)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    assert t("lm.recorded", lang) in journey.text() and "&lt;reason&gt;" in journey.text()
    await journey.click(LocalAction(action="whist", chat=CHAT, value="51:0").pack(), actor=42)
    assert t("lm.warnings", lang) in journey.text()
    assert native.calls == []


async def test_forged_settings_callback_has_no_authority(local_journey, database):
    journey, native = local_journey
    await journey.click(
        LocalAction(action="toggle", chat=CHAT, value="enabled:2").pack(), actor=900
    )
    with use_language("lt"):
        assert native.calls == []
    async with database() as session:
        from app.models import GroupModerationSettings

        assert (await session.get(GroupModerationSettings, CHAT)).enabled


async def test_catalogs_have_all_stage_two_keys():
    keys = {k for k in CATALOGS["lt"] if k.startswith("lm.")}
    assert keys
    for lang in ("en", "ru"):
        assert {k for k in CATALOGS[lang] if k.startswith("lm.")} == keys


async def test_queued_receipt_updates_same_message_after_restart(local_journey, database):
    journey, native = local_journey
    native.error = TelegramRetryAfter(
        method=BanChatMember(chat_id=CHAT, user_id=51), message="Flood", retry_after=60
    )
    await journey.send("/ban 51 1h", actor=42, chat=CHAT)
    assert t("lm.queued") in journey.text()
    sends = len([c for c in journey.transport.calls if isinstance(c, SendMessage)])
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        message_id = draft.panel_message_id
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.operation == "APPLY")
            .values(available_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    native.error = None
    await LocalModerationExecutor(database, journey.bot).process()
    edits = [c for c in journey.transport.calls if isinstance(c, EditMessageText)]
    assert edits[-1].message_id == message_id and edits[-1].chat_id == CHAT
    assert t("lm.done") in edits[-1].text
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == sends
    count = len(edits)
    await LocalModerationExecutor(database, journey.bot).process(limit=0)
    assert len([c for c in journey.transport.calls if isinstance(c, EditMessageText)]) == count


async def test_settings_toggle_preserves_duration_and_rejects_stale_version(
    local_journey, database
):
    journey, native = local_journey
    from app.models import GroupModerationSettings

    async with database() as session:
        await session.execute(
            update(GroupModerationSettings)
            .where(GroupModerationSettings.chat_id == CHAT)
            .values(max_duration_seconds=604800)
        )
        await session.commit()
        version = (await session.get(GroupModerationSettings, CHAT)).version
    data = LocalAction(action="toggle", chat=CHAT, value=f"warnings_enabled:{version}").pack()
    await journey.click(data, actor=42)
    await journey.click(data, actor=42)
    async with database() as session:
        policy = await session.get(GroupModerationSettings, CHAT)
        assert not policy.warnings_enabled
        assert policy.max_duration_seconds == 604800 and policy.version == version + 1
    assert native.calls == []


async def test_unban_before_apply_reports_cancelled_without_api(database):
    native = NativeBot()
    await enabled(database, native)
    await confirmed(
        database, native, await prepared(database, native, until=now() + timedelta(hours=1))
    )
    draft = await prepared(database, native, "UNBAN", key="cancel-before-apply")
    await confirmed(database, native, draft)
    async with database() as session:
        draft = await session.get(GroupModerationDraft, draft.id)
        text, done = await receipt_text(session, draft)
        assert done and t("lm.cancelled") in text
    assert native.calls == []


async def test_private_navigation_retires_pending_receipt(local_journey, database):
    journey, native = local_journey
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="BAN").pack(), actor=42)
    await journey.send("51 1h", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        draft_id = draft.id
    native.error = TelegramRetryAfter(
        method=BanChatMember(chat_id=CHAT, user_id=51), message="Flood", retry_after=60
    )
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    await journey.click(LocalAction(action="group", chat=CHAT).pack(), actor=42)
    count = len(journey.transport.calls)
    async with database() as session:
        assert (await session.get(GroupModerationDraft, draft_id)).receipt_done
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.operation == "APPLY")
            .values(available_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    native.error = None
    await LocalModerationExecutor(database, journey.bot).process()
    assert not any(isinstance(c, EditMessageText) for c in journey.transport.calls[count:])

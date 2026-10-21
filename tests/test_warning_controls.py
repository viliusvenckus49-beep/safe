"""WARN lifecycle, native actions and public controls use real router/SQL paths."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText, RestrictChatMember, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, OTHER, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey

from app.bot.local_moderation import LocalModerationExecutor
from app.errors import DomainError
from app.models import (
    GroupModerationDraft,
    GroupSanction,
    GroupSanctionJob,
    GroupWarningEscalation,
    GroupWarningReset,
    now,
)
from app.moderation_services import GroupModerationService
from app.warning_services import active_warnings, reset_prompt, reset_warnings

pytestmark = pytest.mark.asyncio


async def warn(factory, bot, key, *, chat=CHAT, target=51):
    async with factory() as session:
        service = GroupModerationService(session, bot)
        draft = await service.prepare(
            chat, 42, target, "WARN", until_date=None, reason="Rule", request=key
        )
        sanction = await service.confirm(draft.id, 42)
        return draft.id, sanction.id


async def active(factory, chat=CHAT, target=51):
    async with factory() as session:
        return len(list(await session.scalars(active_warnings(chat, target))))


async def test_third_warning_mutes_once_for_24h_and_only_then_consumes(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "warning-" + str(i)) for i in range(3)]
    assert await active(database) == 3 and bot.calls == []
    async with database() as session:
        event = await session.scalar(select(GroupWarningEscalation))
        assert event.state == "PENDING"
        automatic = await session.get(GroupSanction, event.sanction_id)
        assert 86390 <= (automatic.until_date.replace(tzinfo=UTC) - now()).total_seconds() <= 86400
    worker = LocalModerationExecutor(database, bot)
    await worker.process(sanction_id=ids[-1][1])
    assert await active(database) == 0
    assert len(bot.calls) == 1 and bot.calls[0][0] == "restrict"
    async with database() as session:
        assert (await session.scalar(select(GroupWarningEscalation))).state == "SUCCEEDED"
        assert (
            len(
                list(
                    await session.scalars(
                        select(GroupSanction).where(
                            GroupSanction.kind == "WARN", GroupSanction.status == "COMPLETED"
                        )
                    )
                )
            )
            == 3
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(GroupSanctionJob)
                .where(GroupSanctionJob.operation == "RELEASE")
            )
            == 1
        )
        await GroupModerationService(session, bot).confirm(ids[-1][0], 42)
    await worker.process(sanction_id=ids[-1][1])
    assert len(bot.calls) == 1


@pytest.mark.parametrize("failure", ["reject", "timeout", "rate_limit"])
async def test_failed_or_uncertain_automatic_mute_preserves_all_warnings(database, failure):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "failure-" + str(i)) for i in range(3)]
    method = RestrictChatMember(chat_id=CHAT, user_id=51, permissions=bot.defaults)
    bot.error = (
        TelegramBadRequest(method=method, message="not enough rights")
        if failure == "reject"
        else TimeoutError()
        if failure == "timeout"
        else TelegramRetryAfter(method=method, message="FloodWait", retry_after=30)
    )
    await LocalModerationExecutor(database, bot).process(sanction_id=ids[-1][1])
    assert await active(database) == 3
    async with database() as session:
        event = await session.scalar(select(GroupWarningEscalation))
        assert (
            event.state
            == {"reject": "FAILED", "timeout": "UNCERTAIN", "rate_limit": "PENDING"}[failure]
        )
    assert len(bot.calls) == 1


async def test_warning_counts_are_group_scoped_and_admins_protected(database):
    bot = NativeBot()
    await enabled(database, bot)
    async with database() as session:
        await GroupModerationService(session, bot).configure(
            OTHER, 42, enabled=True, warnings_enabled=True, mutes_enabled=True
        )
    for i in range(2):
        await warn(database, bot, "first-warning-" + str(i))
    await warn(database, bot, "other-group", chat=OTHER)
    assert await active(database) == 2 and await active(database, OTHER) == 1
    assert bot.calls == []
    bot.admin(CHAT, 52)
    with pytest.raises(DomainError, match="protected_target"):
        await warn(database, bot, "admin-protected", target=52)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupWarningEscalation)) == 0


async def test_reset_requires_confirmation_fresh_set_and_live_rights(database):
    bot = NativeBot()
    await enabled(database, bot)
    _, sid = await warn(database, bot, "reset-first")
    async with database() as session:
        local = GroupModerationService(session, bot)
        origin = await session.get(GroupSanction, sid)
        prompt = await reset_prompt(local, origin, 42)
        token = prompt.id
    assert await active(database) == 1
    await warn(database, bot, "reset-new-warning")
    async with database() as session:
        with pytest.raises(DomainError, match="stale_control"):
            await reset_warnings(GroupModerationService(session, bot), token, 42)
        prompt = await reset_prompt(
            GroupModerationService(session, bot), await session.get(GroupSanction, sid), 42
        )
        token = prompt.id
    bot.members[CHAT, 42] = bot.peer(42)
    async with database() as session:
        with pytest.raises(DomainError, match="forbidden"):
            await reset_warnings(GroupModerationService(session, bot), token, 42)
    assert await active(database) == 2
    bot.admin(CHAT, 42)
    async with database() as session:
        await reset_warnings(GroupModerationService(session, bot), token, 42)
    assert await active(database) == 0
    async with database() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(GroupSanction)
                .where(GroupSanction.kind == "WARN", GroupSanction.status == "CANCELLED")
            )
            == 2
        )
        with pytest.raises(DomainError, match="stale_control"):
            await reset_warnings(GroupModerationService(session, bot), token, 42)


async def click(journey, data, *, actor=42, chat=CHAT, message_id):
    journey.sequence += 1
    query = CallbackQuery(
        id=str(journey.sequence),
        from_user=User(id=actor, is_bot=False, first_name="Admin"),
        chat_instance="test",
        data=data,
        message=Message(
            message_id=message_id, date=datetime.now(UTC), chat=Chat(id=chat, type="supergroup")
        ),
    )
    await journey.dp.feed_update(
        journey.bot, Update(update_id=journey.sequence, callback_query=query)
    )


def sent(journey):
    return [call for call in journey.transport.calls if isinstance(call, SendMessage)]


async def test_public_warn_button_private_confirmation_and_double_click(username_journey, database):
    journey, bot = username_journey
    await journey.send("/warn 51", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    data = receipt.reply_markup.inline_keyboard[0][0].callback_data
    async with database() as session:
        draft = await session.scalar(
            select(GroupModerationDraft).where(GroupModerationDraft.kind == "WARN")
        )
        mid = draft.panel_message_id
    before = len(sent(journey))
    await click(journey, data, actor=1, message_id=mid)
    assert len(sent(journey)) == before + 1
    assert (
        isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        and not journey.transport.calls[-1].show_alert
    )
    assert await active(database) == 1
    await click(journey, data, message_id=mid)
    menu = sent(journey)[-1]
    await click(journey, menu.reply_markup.inline_keyboard[1][0].callback_data, message_id=0)
    prompt = sent(journey)[-1]
    assert prompt.ephemeral_message_parameters.receiver_user_id == 42
    yes = prompt.reply_markup.inline_keyboard[0][0].callback_data
    assert await active(database) == 1
    await click(journey, yes, message_id=0)
    assert await active(database) == 0
    edits = [
        call
        for call in journey.transport.calls
        if isinstance(call, EditMessageText) and call.message_id == mid
    ]
    assert edits and edits[-1].reply_markup.inline_keyboard[0][0].callback_data.startswith(
        "mc3|history|"
    )
    await click(journey, yes, message_id=0)
    assert await active(database) == 0 and bot.calls == []


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize("duration", ["", " 30m"])
async def test_release_buttons_are_scoped_authorized_and_idempotent(
    username_journey, database, kind, duration
):
    journey, bot = username_journey
    await journey.send("/" + kind.lower() + " 51" + duration, actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    data = receipt.reply_markup.inline_keyboard[0][0].callback_data
    async with database() as session:
        draft = await session.scalar(
            select(GroupModerationDraft).where(GroupModerationDraft.kind == kind)
        )
        mid = draft.panel_message_id
    applied = len(bot.calls)
    await click(journey, data, actor=1, message_id=mid)
    await click(journey, data, chat=OTHER, message_id=mid)
    await click(journey, data, message_id=mid + 1)
    assert len(bot.calls) == applied
    await click(journey, data, message_id=mid)
    assert len(bot.calls) == applied + 1
    async with database() as session:
        assert (await session.scalar(select(GroupSanction))).status == "REVOKED"
    await click(journey, data, message_id=mid)
    assert len(bot.calls) == applied + 1
    edits = [
        c
        for c in journey.transport.calls
        if isinstance(c, EditMessageText) and c.chat_id == CHAT and c.message_id == mid
    ]
    assert edits[-1].reply_markup is None


async def test_three_public_warnings_publish_one_automatic_mute_with_release_control(
    username_journey, database
):
    journey, bot = username_journey
    for _ in range(3):
        await journey.send("/warn 51", actor=42, chat=CHAT)
    assert await active(database) == 0
    notices = [c for c in sent(journey) if "3 WARN" in c.text]
    assert len(notices) == 1 and notices[0].chat_id == CHAT
    button = notices[0].reply_markup.inline_keyboard[0][0]
    assert button.text == "🔊 UNMUTE"
    assert len(bot.calls) == 1
    async with database() as session:
        event = await session.scalar(select(GroupWarningEscalation))
        draft = await session.get(GroupModerationDraft, event.draft_id)
        mid = draft.panel_message_id
    await click(journey, button.callback_data, message_id=mid)
    assert len(bot.calls) == 2
    assert await active(database) == 0


async def test_cancelling_warns_cancels_only_unsent_escalation_and_retains_history(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "cancel-before-" + str(i)) for i in range(3)]
    async with database() as session:
        local = GroupModerationService(session, bot)
        prompt = await reset_prompt(local, await session.get(GroupSanction, ids[-1][1]), 42)
        await reset_warnings(local, prompt.id, 42)
    await LocalModerationExecutor(database, bot).process()
    assert await active(database) == 0 and bot.calls == []
    async with database() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(GroupSanction).where(GroupSanction.kind == "WARN")
            )
            == 3
        )
        assert (await session.scalar(select(GroupWarningEscalation))).state == "CANCELLED"


async def test_uncertain_automatic_mute_cannot_be_cancelled_as_if_it_never_happened(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "uncertain-" + str(i)) for i in range(3)]
    bot.error = TimeoutError()
    await LocalModerationExecutor(database, bot).process(sanction_id=ids[-1][1])
    async with database() as session:
        local = GroupModerationService(session, bot)
        prompt = await reset_prompt(local, await session.get(GroupSanction, ids[-1][1]), 42)
        with pytest.raises(DomainError, match="busy"):
            await reset_warnings(local, prompt.id, 42)
    assert await active(database) == 3 and len(bot.calls) == 1


async def test_postgres_concurrent_warning_delivery_and_reset(postgres_contract):
    import asyncio

    factory, _ = postgres_contract
    bot = NativeBot()
    await enabled(factory, bot)
    delivered = await asyncio.gather(
        warn(factory, bot, "same-warning-delivery"), warn(factory, bot, "same-warning-delivery")
    )
    assert delivered[0] == delivered[1] and await active(factory) == 1
    await asyncio.gather(
        warn(factory, bot, "parallel-warning-two"), warn(factory, bot, "parallel-warning-three")
    )
    assert await active(factory) == 3
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GroupWarningEscalation)) == 1
        origin = await session.get(GroupSanction, delivered[0][1])
        prompt = await reset_prompt(GroupModerationService(session, bot), origin, 42)
        token = prompt.id

    async def reset():
        async with factory() as session:
            try:
                await reset_warnings(GroupModerationService(session, bot), token, 42)
                return "ok"
            except DomainError:
                return "stale"

    assert sorted(await asyncio.gather(reset(), reset())) == ["ok", "stale"]
    assert await active(factory) == 0 and bot.calls == []


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_warn_receipt_confirmation_and_history_are_localized(
    username_journey, database, settings, lang
):
    from app.i18n import t, use_language
    from app.services import Service

    journey, _ = username_journey
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await journey.send("/warn 51 reason", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    with use_language(lang):
        assert t("lm.control.warn_title") in receipt.text and "1/3" in receipt.text
        assert receipt.reply_markup.inline_keyboard[0][0].text == t("lm.control.unwarn")
    # Unknown target identity must not print its ID twice.
    assert receipt.text.count("51") == 1
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        mid = draft.panel_message_id
    await click(journey, receipt.reply_markup.inline_keyboard[0][0].callback_data, message_id=mid)
    menu = sent(journey)[-1]
    await click(journey, menu.reply_markup.inline_keyboard[1][0].callback_data, message_id=0)
    prompt = sent(journey)[-1]
    assert (
        prompt.parse_mode == "HTML" and prompt.ephemeral_message_parameters.receiver_user_id == 42
    )
    await click(journey, prompt.reply_markup.inline_keyboard[0][0].callback_data, message_id=0)
    await journey.send("/warnings 51", actor=42, chat=CHAT)
    with use_language(lang):
        assert t("lm.control.warn_cancelled") in journey.text()


async def test_expired_and_foreign_confirmation_tokens_cannot_clear_warns(
    username_journey, database
):
    journey, _ = username_journey
    await journey.send("/warn 51", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        mid = draft.panel_message_id
    await click(journey, receipt.reply_markup.inline_keyboard[0][0].callback_data, message_id=mid)
    menu = sent(journey)[-1]
    await click(journey, menu.reply_markup.inline_keyboard[1][0].callback_data, message_id=0)
    token = sent(journey)[-1].reply_markup.inline_keyboard[0][0].callback_data
    await click(journey, token, actor=1, message_id=0)
    await click(journey, token, chat=OTHER, message_id=0)
    assert await active(database) == 1
    async with database() as session:
        prompt = await session.scalar(select(GroupWarningReset))
        prompt.expires_at = now() - timedelta(seconds=1)
        await session.commit()
    await click(journey, token, message_id=0)
    assert await active(database) == 1


async def test_auto_mute_rechecks_protected_admin_and_preserves_warns(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "role-change-" + str(i)) for i in range(3)]
    bot.admin(CHAT, 51)
    await LocalModerationExecutor(database, bot).process(sanction_id=ids[-1][1])
    assert await active(database) == 3 and bot.calls == []
    async with database() as session:
        event = await session.scalar(select(GroupWarningEscalation))
        assert event.state in {"FAILED", "CANCELLED"}


@pytest.mark.parametrize("flood_wait", [False, True])
async def test_announcement_queue_skips_published_rows_and_respects_flood_wait(
    database, monkeypatch, flood_wait
):
    from uuid import uuid4

    from app.bot import moderation_controls as controls

    bot = NativeBot()
    await enabled(database, bot)
    last_id = None
    async with database() as session:
        for index in range(11):
            warning = GroupSanction(
                chat_id=CHAT,
                telegram_id=200 + index,
                actor_id=42,
                kind="WARN",
                reason="",
                status="COMPLETED",
                policy_version=1,
                request_key="outbox-warning-" + str(index),
            )
            mute = GroupSanction(
                chat_id=CHAT,
                telegram_id=200 + index,
                actor_id=42,
                kind="MUTE",
                reason="3 WARN",
                status="ACTIVE",
                owned=True,
                until_date=now() + timedelta(days=1),
                policy_version=1,
                request_key="outbox-mute-" + str(index),
            )
            session.add_all([warning, mute])
            await session.flush()
            trigger = GroupModerationDraft(
                id=uuid4().hex,
                chat_id=CHAT,
                telegram_id=200 + index,
                actor_id=42,
                kind="WARN",
                reason="",
                status="CONFIRMED",
                sanction_id=warning.id,
                request_key="outbox-trigger-" + str(index),
                expires_at=now(),
                panel_chat_id=CHAT,
                panel_message_id=100 + index,
            )
            announcement = GroupModerationDraft(
                id=uuid4().hex,
                chat_id=CHAT,
                telegram_id=200 + index,
                actor_id=42,
                kind="MUTE",
                reason="3 WARN",
                status="CONFIRMED",
                sanction_id=mute.id,
                request_key="outbox-public-" + str(index),
                expires_at=now(),
                panel_chat_id=CHAT if index < 10 else None,
                panel_message_id=200 + index if index < 10 else None,
            )
            session.add_all([trigger, announcement])
            await session.flush()
            session.add(
                GroupWarningEscalation(
                    chat_id=CHAT,
                    telegram_id=200 + index,
                    trigger_id=warning.id,
                    sanction_id=mute.id,
                    draft_id=announcement.id,
                    state="SUCCEEDED",
                )
            )
            last_id = announcement.id
        await session.commit()
    calls = []

    async def publish(session, bot, draft, **kwargs):
        calls.append(draft.id)
        if flood_wait:
            raise TelegramRetryAfter(
                method=SendMessage(chat_id=CHAT, text="test"), message="FloodWait", retry_after=30
            )
        draft.panel_chat_id, draft.panel_message_id = CHAT, 999
        await session.commit()

    monkeypatch.setattr(controls, "public_receipt", publish)
    await controls.publish_warning_announcements(database, bot)
    await controls.publish_warning_announcements(database, bot)
    assert calls == [last_id]
    async with database() as session:
        last = await session.get(GroupModerationDraft, last_id)
        if flood_wait:
            assert (
                last.receipt_checked_at is None
                and last.receipt_available_at.replace(tzinfo=UTC) > now()
            )
        else:
            assert last.panel_message_id == 999

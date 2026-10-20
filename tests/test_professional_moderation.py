"""Real routing/SQL; Telegram is a stateful API double, not a live account."""

import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import RestrictChatMember
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_warning_controls import active, click, sent, warn

from app.bot.callbacks import ModerationControl
from app.bot.local_moderation import MUTE_PERMISSIONS, LocalModerationExecutor
from app.errors import DomainError
from app.i18n import t, use_language
from app.models import (
    AuditEvent,
    GroupSanction,
    GroupSanctionJob,
)
from app.moderation_services import GroupModerationService
from app.warning_services import reset_prompt, reset_warnings

pytestmark = pytest.mark.asyncio


async def test_selected_warning_retains_all_history_and_audits_only_selected_id(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "selected-" + str(n)) for n in range(2)]
    async with database() as session:
        service = GroupModerationService(session, bot)
        prompt = await reset_prompt(
            service, await session.get(GroupSanction, ids[0][1]), 42, selected_warning_id=ids[1][1]
        )
        await reset_warnings(service, prompt.id, 42)
        assert (await session.get(GroupSanction, ids[0][1])).status == "RECORDED"
        assert (await session.get(GroupSanction, ids[1][1])).status == "CANCELLED"
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "group_moderation_warnings_cancelled")
        )
        assert audit.details["warning_ids"] == [ids[1][1]]
        with pytest.raises(DomainError, match="stale"):
            await reset_warnings(service, prompt.id, 42)
    assert await active(database) == 1 and not bot.calls


async def test_selected_warning_rejects_another_user_or_group(database):
    bot = NativeBot()
    await enabled(database, bot)
    first = await warn(database, bot, "selected-own")
    other = await warn(database, bot, "selected-other", target=52)
    async with database() as session:
        service = GroupModerationService(session, bot)
        with pytest.raises(DomainError, match="stale"):
            await reset_prompt(
                service,
                await session.get(GroupSanction, first[1]),
                42,
                selected_warning_id=other[1],
            )
    assert await active(database) == 1 and await active(database, target=52) == 1


async def test_individual_reset_cannot_clear_consumed_warnings_or_lift_auto_mute(database):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "consumed-" + str(n)) for n in range(3)]
    await LocalModerationExecutor(database, bot).process(sanction_id=ids[-1][1])
    async with database() as session:
        with pytest.raises(DomainError, match="stale"):
            await reset_prompt(
                GroupModerationService(session, bot),
                await session.get(GroupSanction, ids[0][1]),
                42,
                selected_warning_id=ids[0][1],
            )
    assert bot.members[CHAT, 51].status == "restricted" and len(bot.calls) == 1


async def test_postgres_individual_and_all_confirmations_serialize(postgres_contract):
    factory, _ = postgres_contract
    bot = NativeBot()
    await enabled(factory, bot)
    bot.admin(CHAT, 43)
    ids = [await warn(factory, bot, "parallel-selected-" + str(n)) for n in range(2)]
    async with factory() as session:
        service = GroupModerationService(session, bot)
        origin = await session.get(GroupSanction, ids[0][1])
        one = await reset_prompt(service, origin, 42, selected_warning_id=origin.id)
        all_rows = await reset_prompt(service, origin, 43)
        tokens = [(one.id, 42), (all_rows.id, 43)]

    async def run(token, actor):
        async with factory() as session:
            try:
                await reset_warnings(GroupModerationService(session, bot), token, actor)
                return "ok"
            except DomainError:
                return "stale"

    assert sorted(await asyncio.gather(*(run(*x) for x in tokens))) == ["ok", "stale"]
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(GroupSanction).where(GroupSanction.kind == "WARN")
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "group_moderation_warnings_cancelled")
            )
            == 1
        )


async def test_warn_control_specific_selection_history_and_private_confirmation(
    username_journey, database
):
    journey, native = username_journey
    await journey.send("/warn 51 first-reason", actor=42, chat=CHAT)
    await journey.send("/warn 51 second-reason", actor=42, chat=CHAT)
    await journey.send("/warn 52 OTHER-USER-SECRET", actor=42, chat=CHAT)
    async with database() as session:
        origin = await session.scalar(
            select(GroupSanction).where(GroupSanction.telegram_id == 51).order_by(GroupSanction.id)
        )
        oid = origin.id
    await click(journey, ModerationControl(action="history", sanction=oid).pack(), message_id=0)
    history = sent(journey)[-1]
    assert (
        "first-reason" in history.text
        and "second-reason" in history.text
        and "OTHER-USER-SECRET" not in history.text
    )
    assert history.ephemeral_message_parameters.receiver_user_id == 42
    await journey.send(f"/unwarn 51 {oid}", actor=42, chat=CHAT)
    confirm = sent(journey)[-1]
    assert (
        f"WARN #{oid}" in confirm.text
        and "first-reason" in confirm.text
        and "second-reason" not in confirm.text
    )
    assert confirm.ephemeral_message_parameters.receiver_user_id == 42
    await click(journey, confirm.reply_markup.inline_keyboard[0][0].callback_data, message_id=0)
    assert await active(database) == 1 and await active(database, target=52) == 1
    assert not native.calls


@pytest.mark.parametrize("command,title", [("mute", "already_muted"), ("ban", "already_banned")])
async def test_duplicate_sanction_has_specific_private_reply_without_new_native_write(
    username_journey, command, title
):
    journey, native = username_journey
    await journey.send("/" + command + " 51", actor=42, chat=CHAT)
    count = len(native.calls)
    await journey.send("/" + command + " 51", actor=42, chat=CHAT)
    response = sent(journey)[-1]
    assert t("lm.diagnostic." + title + ".title") in response.text
    assert response.ephemeral_message_parameters.receiver_user_id == 42
    assert len(native.calls) == count


@pytest.mark.parametrize(
    "command,title", [("unmute", "already_unmuted"), ("unban", "already_unbanned")]
)
async def test_already_clear_is_verified_and_private(username_journey, command, title):
    journey, native = username_journey
    await journey.send("/" + command + " 51", actor=42, chat=CHAT)
    response = sent(journey)[-1]
    assert t("lm.diagnostic." + title + ".title") in response.text
    assert response.ephemeral_message_parameters.receiver_user_id == 42 and not native.calls


@pytest.mark.parametrize("failure", ["reject", "timeout", "rate_limit"])
async def test_failed_or_delayed_native_action_never_posts_public_failure(
    username_journey, database, failure
):
    journey, native = username_journey
    method = RestrictChatMember(chat_id=CHAT, user_id=51, permissions=MUTE_PERMISSIONS)
    native.error = (
        TimeoutError()
        if failure == "timeout"
        else TelegramRetryAfter(method=method, message="Flood", retry_after=60)
        if failure == "rate_limit"
        else TelegramBadRequest(method=method, message="REJECTED")
    )
    await journey.send("/mute@redsafecheckbot 51 30m", actor=42, chat=CHAT)
    responses = sent(journey)
    assert responses and all(
        m.ephemeral_message_parameters.receiver_user_id == 42 for m in responses
    )
    expected = {"reject": "api_rejected", "timeout": "uncertain", "rate_limit": "queued"}[failure]
    assert t("lm.diagnostic." + expected + ".title") in responses[-1].text
    assert "/mute@redsafecheckbot" in responses[-1].text
    async with database() as session:
        jobs = list(await session.scalars(select(GroupSanctionJob)))
        if failure == "reject":
            release = next(j for j in jobs if j.operation == "RELEASE")
            assert release.status == "CANCELLED" and release.result == "NO_CONFIRMED_RESTRICTION"
        elif failure == "timeout":
            assert next(j for j in jobs if j.operation == "APPLY").status == "UNCERTAIN"


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_new_catalog_complete_and_html_formatted(lang):
    from app.locales.moderation_control import CATALOGS as CONTROL

    assert set(CONTROL[lang]) == set(CONTROL["lt"])
    with use_language(lang):
        for key, value in CONTROL[lang].items():
            if isinstance(value, tuple):
                from app.bot.moderation_errors import error_message

                result = error_message("/mute", key, fallback="")
                assert result.startswith("<b><u>") and "{command}" not in result
        assert "𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗" in t("lm.control.warn_title")


async def test_warn_control_falls_back_to_dm_and_confirmation_still_works(
    username_journey, database
):
    from aiogram.methods import SendMessage

    journey, native = username_journey
    await journey.send("/warn 51", actor=42, chat=CHAT)
    original = journey.transport.make_request

    async def request(bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.ephemeral_message_parameters is not None:
            raise TelegramBadRequest(method=method, message="EPHEMERAL_NOT_SUPPORTED")
        return await original(bot, method, timeout)

    journey.transport.make_request = request
    await journey.send("/unwarn 51 1", actor=42, chat=CHAT)
    confirmation = sent(journey)[-1]
    assert confirmation.chat_id == 42 and confirmation.ephemeral_message_parameters is None
    await journey.click(confirmation.reply_markup.inline_keyboard[0][0].callback_data, actor=42)
    assert await active(database) == 0 and native.calls == []


async def test_pending_private_delivery_timeout_does_not_hide_later_public_success(
    username_journey, database
):
    from aiogram.exceptions import TelegramNetworkError
    from aiogram.methods import SendMessage
    from sqlalchemy import update

    from app.models import GroupModerationDraft, now

    journey, native = username_journey
    original = journey.transport.make_request

    async def request(bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.ephemeral_message_parameters is not None:
            raise TelegramNetworkError(method=method, message="AMBIGUOUS_DELIVERY")
        return await original(bot, method, timeout)

    journey.transport.make_request = request
    native.error = TelegramRetryAfter(
        method=RestrictChatMember(chat_id=CHAT, user_id=51, permissions=MUTE_PERMISSIONS),
        message="Flood",
        retry_after=60,
    )
    await journey.send("/mute 51", actor=42, chat=CHAT)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        assert draft.receipt_visibility == "PRIVATE" and draft.panel_message_id is None
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.operation == "APPLY")
            .values(available_at=now())
        )
        await session.commit()
    native.error = None
    await LocalModerationExecutor(database, journey.bot).process()
    public = [
        m for m in sent(journey) if m.chat_id == CHAT and m.ephemeral_message_parameters is None
    ]
    assert len(public) == 1 and "𝗨𝗦𝗘𝗥 • 𝗠𝗨𝗧𝗘𝗗" in public[0].text


@pytest.mark.parametrize("count", [3, 4])
async def test_individual_unwarn_only_cancels_unsent_auto_mute_below_threshold(database, count):
    bot = NativeBot()
    await enabled(database, bot)
    ids = [await warn(database, bot, "individual-threshold-" + str(n)) for n in range(count)]
    async with database() as session:
        service = GroupModerationService(session, bot)
        origin = await session.get(GroupSanction, ids[0][1])
        prompt = await reset_prompt(service, origin, 42, selected_warning_id=origin.id)
        await reset_warnings(service, prompt.id, 42)
    await LocalModerationExecutor(database, bot).process()
    assert len(bot.calls) == (1 if count == 4 else 0)
    assert await active(database) == (0 if count == 4 else 2)


async def test_private_menu_already_unmuted_uses_same_verified_explanation(
    username_journey, database
):
    from app.bot.callbacks import LocalAction
    from app.models import GroupModerationDraft

    journey, native = username_journey
    await journey.send("/moderation", actor=42)
    await journey.click(LocalAction(action="action", chat=CHAT, value="UNMUTE").pack(), actor=42)
    await journey.send("51", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        draft_id = draft.id
    await journey.click(LocalAction(action="confirm", value=draft_id).pack(), actor=42)
    assert t("lm.diagnostic.already_unmuted.title") in journey.text() and not native.calls

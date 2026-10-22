"""Configurable warning policy, durable history, races and native UI journeys.

Telegram methods use stateful doubles; PostgreSQL checks use isolated schemas.
"""

import asyncio
from datetime import UTC, timedelta

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, OTHER, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_warning_controls import active, warn

from app.bot.callbacks import WarningSettings
from app.bot.keyboards import action
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_messages import success_message
from app.bot.warning_settings import cb
from app.errors import DomainError
from app.i18n import t, use_language
from app.models import (
    AuditEvent,
    GroupModerationDraft,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    GroupWarningEscalation,
    now,
)
from app.moderation_services import GroupModerationService
from app.services import Service
from app.warning_services import configure_warnings, reset_prompt, reset_warnings

pytestmark = pytest.mark.asyncio


async def configure(
    factory, bot, *, chat=CHAT, limit=3, punishment="MUTE", seconds=86400, version=None, actor=42
):
    async with factory() as session:
        policy = await session.get(GroupModerationSettings, chat)
        return await configure_warnings(
            GroupModerationService(session, bot),
            chat,
            actor,
            expected_version=version
            if version is not None
            else (policy.warning_version if policy else 0),
            limit=limit,
            action=punishment,
            mute_seconds=seconds,
        )


@pytest.mark.parametrize("limit", range(2, 7))
@pytest.mark.parametrize("punishment", ["OFF", "MUTE", "BAN", "KICK"])
async def test_each_limit_and_action_use_existing_history_and_executor(database, limit, punishment):
    bot = NativeBot()
    await enabled(database, bot)
    await configure(database, bot, limit=limit, punishment=punishment, seconds=3600)
    ids = [await warn(database, bot, f"policy-{n}") for n in range(limit)]
    async with database() as session:
        events = list(await session.scalars(select(GroupWarningEscalation)))
        if punishment == "OFF":
            assert not events
        else:
            assert len(events) == 1 and events[0].warning_limit == limit
            row = await session.get(GroupSanction, events[0].sanction_id)
            assert row.kind == punishment and row.reason == f"{limit} WARN"
            if punishment == "MUTE":
                assert 3590 < (row.until_date.replace(tzinfo=UTC) - now()).total_seconds() <= 3600
            else:
                assert row.until_date is None
        draft = await session.get(GroupModerationDraft, ids[-1][0])
        text = await success_message(session, draft, await session.get(GroupSanction, ids[-1][1]))
        assert f"{limit}/{limit}" in text
    assert await active(database) == limit and not bot.calls
    worker = LocalModerationExecutor(database, bot)
    await worker.process(limit=1, sanction_id=ids[-1][1])
    if punishment == "OFF":
        assert await active(database) == limit and not bot.calls
        return
    if punishment == "KICK":
        assert await active(database) == limit  # A ban alone is not a completed kick.
        await worker.process(limit=1, sanction_id=ids[-1][1])
        assert [c[0] for c in bot.calls] == ["ban", "unban"]
    else:
        assert [c[0] for c in bot.calls] == ["restrict" if punishment == "MUTE" else "ban"]
    assert await active(database) == 0
    async with database() as session:
        assert (await session.scalar(select(GroupWarningEscalation))).state == "SUCCEEDED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(GroupSanction)
                .where(GroupSanction.kind == "WARN", GroupSanction.status == "COMPLETED")
            )
            == limit
        )
    before = len(bot.calls)
    await worker.process(limit=5)
    assert len(bot.calls) == before


@pytest.mark.parametrize("punishment", ["MUTE", "BAN", "KICK"])
@pytest.mark.parametrize("failure", ["false", "timeout", "retry"])
async def test_failed_or_ambiguous_action_never_consumes_warnings(database, punishment, failure):
    bot = NativeBot()
    await enabled(database, bot)
    await configure(database, bot, limit=2, punishment=punishment)
    await warn(database, bot, "fail-first")
    _, warning_id = await warn(database, bot, "fail-second")
    if failure == "false":
        bot.api_false = True
    elif failure == "timeout":
        bot.error = TimeoutError()
    else:
        bot.error = TelegramRetryAfter(method="apply", message="wait", retry_after=10)
    await LocalModerationExecutor(database, bot).process(sanction_id=warning_id)
    assert await active(database) == 2 and len(bot.calls) == 1


async def test_kick_release_timeout_preserves_warnings_and_is_not_replayed(database):
    bot = NativeBot()
    await enabled(database, bot)
    await configure(database, bot, limit=2, punishment="KICK")
    await warn(database, bot, "kick-first")
    _, target = await warn(database, bot, "kick-second")
    worker = LocalModerationExecutor(database, bot)
    await worker.process(sanction_id=target)

    async def failed_unban(**kwargs):
        bot.calls.append(("unban", kwargs))
        raise TimeoutError()

    bot.unban_chat_member = failed_unban
    await worker.process(sanction_id=target)
    await worker.process(sanction_id=target)
    assert [r[0] for r in bot.calls] == ["ban", "unban"]
    assert await active(database) == 2

    async with database() as session:
        assert (await session.scalar(select(GroupWarningEscalation))).state == "UNCERTAIN"


async def test_policy_changes_preserve_manual_jobs_and_other_groups(database):
    bot = NativeBot()
    await enabled(database, bot)
    async with database() as session:
        await GroupModerationService(session, bot).configure(
            OTHER, 42, enabled=True, warnings_enabled=True, mutes_enabled=True
        )
        manual = await GroupModerationService(session, bot).submit(
            CHAT, 42, 52, "BAN", until_date=None, reason="manual", key="manual-job"
        )
    await configure(database, bot, limit=2)
    await warn(database, bot, "cancel-auto-1")
    await warn(database, bot, "cancel-auto-2")
    changed = await configure(database, bot, limit=6, punishment="OFF")
    async with database() as session:
        assert (await session.get(GroupSanction, manual.id)).status == "PENDING"
        assert (await session.scalar(select(GroupWarningEscalation))).state == "CANCELLED"
        other = await session.get(GroupModerationSettings, OTHER)
        assert (other.warning_limit, other.warning_action, other.warning_mute_seconds) == (
            3,
            "MUTE",
            86400,
        )
        assert (
            await session.get(GroupModerationSettings, CHAT)
        ).warning_version == changed.warning_version
        assert await session.scalar(
            select(AuditEvent.id).where(AuditEvent.action == "group_moderation_warning_settings")
        )
    assert await active(database) == 2
    await LocalModerationExecutor(database, bot).process(sanction_id=manual.id)
    assert bot.calls[0][0] == "ban" and bot.calls[0][1]["user_id"] == 52


async def test_config_change_is_blocked_after_claim_and_stale_versions_reject(database):
    bot = NativeBot()
    await enabled(database, bot)
    previous = await configure(database, bot, limit=2)
    with pytest.raises(DomainError, match="request_conflict"):
        await configure(database, bot, limit=5, version=previous.warning_version - 1)
    await warn(database, bot, "claimed-auto-1")
    await warn(database, bot, "claimed-auto-2")
    claimed = await LocalModerationExecutor(database, bot).claim()
    assert claimed is not None
    with pytest.raises(DomainError, match="busy"):
        await configure(database, bot, punishment="OFF")
    assert await active(database) == 2


async def test_reset_uses_escalation_threshold_snapshot(database):
    bot = NativeBot()
    await enabled(database, bot)
    await configure(database, bot, limit=5)
    ids = [await warn(database, bot, f"warning-five-{i}") for i in range(5)]
    async with database() as session:
        local = GroupModerationService(session, bot)
        origin = await session.get(GroupSanction, ids[-1][1])
        prompt = await reset_prompt(local, origin, 42, selected_warning_id=ids[0][1])
        await reset_warnings(local, prompt.id, 42)
        assert (await session.scalar(select(GroupWarningEscalation))).state == "CANCELLED"
    await LocalModerationExecutor(database, bot).process()
    assert not bot.calls and await active(database) == 4


@pytest.mark.parametrize("punishment", ["BAN", "KICK"])
async def test_warning_escalation_can_replace_existing_mute_without_unmuting_it(
    database, punishment
):
    bot = NativeBot()
    await enabled(database, bot)
    async with database() as session:
        original = await GroupModerationService(session, bot).submit(
            CHAT,
            42,
            51,
            "MUTE",
            until_date=now() + timedelta(hours=1),
            reason="Existing",
            key="existing-mute",
        )
    worker = LocalModerationExecutor(database, bot)
    await worker.process(sanction_id=original.id)
    await configure(database, bot, limit=2, punishment=punishment)
    await warn(database, bot, "stronger-warning-first")
    _, trigger = await warn(database, bot, "stronger-warning-second")
    await worker.process(limit=2, sanction_id=trigger)
    assert await active(database) == 0
    async with database() as session:
        assert (await session.get(GroupSanction, original.id)).status == "COMPLETED"
        release = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == original.id, GroupSanctionJob.operation == "RELEASE"
            )
        )
        assert release.status == "CANCELLED"
    assert [r[0] for r in bot.calls] == (
        ["restrict", "ban"] if punishment == "BAN" else ["restrict", "ban", "unban"]
    )


@pytest.mark.parametrize("actor", [1, 900])
async def test_global_role_never_bypasses_live_group_configuration_rights(database, actor):
    bot = NativeBot()
    await enabled(database, bot)
    with pytest.raises(DomainError, match="forbidden"):
        await configure(database, bot, actor=actor, limit=2, punishment="BAN")


def screen(journey):
    return next(
        c
        for c in reversed(journey.transport.calls)
        if isinstance(c, (SendMessage, EditMessageText))
    )


def buttons(journey):
    return [b for row in screen(journey).reply_markup.inline_keyboard for b in row]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_photo_layout_localization_edit_only_and_back_navigation(
    local_journey, database, settings, lang
):
    journey, native = local_journey
    async with database() as session:
        service = Service(settings, session)
        await service.set_language(42, lang)
        await service.access.change(900, "42", True, "a" * 32)
    await journey.click(action("group_help"), actor=42)
    from app.bot.group_help import cb as gh_cb

    assert gh_cb("group", CHAT) in [b.callback_data for b in buttons(journey)]
    await journey.click(gh_cb("group", CHAT), actor=42)
    await journey.click(cb("policy", CHAT), actor=42)
    menu = screen(journey)
    assert isinstance(menu, EditMessageText) and "<b>" in menu.text and "<u>" in menu.text
    assert menu.parse_mode == "HTML"
    assert [len(row) for row in menu.reply_markup.inline_keyboard] == [1, 2, 2, 1, 5, 1]
    with use_language(lang):
        assert menu.text == t("ws.menu", punishment=t("ws.name.MUTE"), limit=3)
    sent = len([c for c in journey.transport.calls if isinstance(c, SendMessage)])
    choose = next(b.callback_data for b in buttons(journey) if b.text == "5")
    await journey.click(choose, actor=42)
    assert "5 ✅" in [b.text for b in buttons(journey)]
    mute = next(
        b.callback_data
        for b in buttons(journey)
        if b.callback_data.startswith("ws|")
        and WarningSettings.unpack(b.callback_data).action == "duration"
    )
    await journey.click(mute, actor=42)
    duration = next(
        b.callback_data
        for b in buttons(journey)
        if WarningSettings.unpack(b.callback_data).value == "3600"
    )
    await journey.click(duration, actor=42)
    off = next(
        b.callback_data
        for b in buttons(journey)
        if b.callback_data.startswith("ws|")
        and WarningSettings.unpack(b.callback_data).value == "OFF"
    )
    await journey.click(off, actor=42)
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == sent
    async with database() as session:
        policy = await session.get(GroupModerationSettings, CHAT)
        assert (policy.warning_limit, policy.warning_action, policy.warning_mute_seconds) == (
            5,
            "OFF",
            3600,
        )
    await journey.click(cb("warned", CHAT), actor=42)
    await journey.click(cb("policy", CHAT), actor=42)
    await journey.click(cb("groups"), actor=42)
    assert action("group_help") in [b.callback_data for b in buttons(journey)]
    assert not native.calls


async def test_member_list_is_scoped_and_preserves_existing_reason_date_admin(
    local_journey, database
):
    journey, native = local_journey
    await configure(database, native, punishment="OFF")
    await warn(database, native, "listed-warning")
    await journey.click(cb("warned", CHAT), actor=42)
    assert any(b.callback_data == cb("member", CHAT, "51") for b in buttons(journey))
    await journey.click(cb("member", CHAT, "51"), actor=42)
    assert "Rule" in screen(journey).text and "<code>42</code>" in screen(journey).text
    await journey.click(cb("policy", CHAT), actor=1)
    assert (
        isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        and journey.transport.calls[-1].show_alert
    )


async def test_stale_setting_click_is_private_and_cannot_overwrite_new_policy(
    local_journey, database
):
    journey, native = local_journey
    await journey.click(cb("policy", CHAT), actor=42)
    previous = next(b.callback_data for b in buttons(journey) if b.text == "2")
    selected = next(b.callback_data for b in buttons(journey) if b.text == "4")
    await journey.click(selected, actor=42)
    await journey.click(previous, actor=42)
    assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
    assert journey.transport.calls[-1].show_alert
    assert "4 ✅" in [b.text for b in buttons(journey)]
    async with database() as session:
        assert (await session.get(GroupModerationSettings, CHAT)).warning_limit == 4
    assert not any(isinstance(c, SendMessage) for c in journey.transport.calls)
    assert not native.calls
    await journey.click(cb("policy", CHAT), actor=42, chat=CHAT)
    assert (
        isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        and journey.transport.calls[-1].show_alert
    )


async def test_postgres_concurrent_settings_use_one_version_and_per_group_isolation(
    postgres_contract,
):
    factory, _ = postgres_contract
    bot = NativeBot()
    await enabled(factory, bot)
    async with factory() as session:
        version = (await session.get(GroupModerationSettings, CHAT)).warning_version

    async def change(limit):
        try:
            await configure(factory, bot, limit=limit, version=version)
            return "ok"
        except DomainError as error:
            return error.code

    results = await asyncio.gather(change(2), change(6))
    assert sorted(results) == ["group_moderation_request_conflict", "ok"]


async def test_postgres_two_warnings_and_workers_do_not_duplicate_configured_ban(postgres_contract):
    factory, _ = postgres_contract
    bot = NativeBot()
    await enabled(factory, bot)
    await configure(factory, bot, limit=2, punishment="BAN")
    await asyncio.gather(
        warn(factory, bot, "parallel-first"), warn(factory, bot, "parallel-second")
    )
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GroupWarningEscalation)) == 1
    await asyncio.gather(
        LocalModerationExecutor(factory, bot).process(limit=5),
        LocalModerationExecutor(factory, bot).process(limit=5),
    )
    assert [call[0] for call in bot.calls] == ["ban"]
    assert await active(factory) == 0


@pytest.mark.parametrize("value", [1, 7, True, "3", None])
async def test_invalid_limit_is_rejected_without_policy_change(database, value):
    bot = NativeBot()
    await enabled(database, bot)
    with pytest.raises(DomainError, match="invalid_settings"):
        await configure(database, bot, limit=value)
    async with database() as session:
        assert (await session.get(GroupModerationSettings, CHAT)).warning_limit == 3
    assert not bot.calls

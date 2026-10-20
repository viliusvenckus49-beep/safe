"""Owned MUTE transitions: real SQL and a stateful Telegram API double."""

import asyncio
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import BanChatMember, EditMessageText
from sqlalchemy import select
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_warning_controls import click, sent

from app.bot.callbacks import ModerationControl
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_errors import diagnose_state
from app.errors import DomainError
from app.i18n import t
from app.models import AuditEvent, GroupModerationDraft, GroupSanction, GroupSanctionJob, now
from app.moderation_services import GroupModerationService

pytestmark = pytest.mark.asyncio


async def request(factory, bot, kind, key, until=None):
    async with factory() as session:
        row = await GroupModerationService(session, bot).submit(
            CHAT, 42, 51, kind, until_date=until, reason="transition", key=key
        )
        return row.id


async def muted(factory, bot):
    await enabled(factory, bot)
    mid = await request(factory, bot, "MUTE", "initial-mute", now() + timedelta(days=1))
    worker = LocalModerationExecutor(factory, bot)
    await worker.process(sanction_id=mid)
    return mid, worker


@pytest.mark.parametrize("kind,temporary", [("BAN", False), ("BAN", True), ("KICK", False)])
async def test_owned_mute_can_be_replaced_without_unmute_and_history_is_retained(
    database, kind, temporary
):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    new = await request(
        database, bot, kind, "replace-mute", now() + timedelta(hours=1) if temporary else None
    )
    await worker.process(limit=2, sanction_id=new)
    assert [call[0] for call in bot.calls] == (
        ["restrict", "ban", "unban"] if kind == "KICK" else ["restrict", "ban"]
    )
    async with database() as session:
        assert (await session.get(GroupSanction, mid)).status == "COMPLETED"
        replacement = await session.get(GroupSanction, new)
        assert replacement.replaces_sanction_id == mid and replacement.owned
        release = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == mid, GroupSanctionJob.operation == "RELEASE"
            )
        )
        assert release.status == "CANCELLED" and release.result == "REPLACED"
        event = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "group_moderation_sanction_replaced")
        )
        assert event.details["replacement_id"] == new
        with pytest.raises(DomainError):
            await GroupModerationService(session, bot).revoke(CHAT, 42, mid, key="old-mute-control")
    assert len(bot.calls) == (3 if kind == "KICK" else 2)


@pytest.mark.parametrize("error", ["reject", "false", "timeout", "retry"])
async def test_rejected_or_unknown_replacement_never_discards_original_mute(database, error):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    new = await request(database, bot, "BAN", "replacement-error")
    bot.error = {
        "reject": TelegramBadRequest(
            method=BanChatMember(chat_id=CHAT, user_id=51), message="not enough rights"
        ),
        "timeout": TimeoutError(),
        "retry": TelegramRetryAfter(
            method=BanChatMember(chat_id=CHAT, user_id=51), message="wait", retry_after=1
        ),
    }.get(error)
    bot.api_false = error == "false"
    await worker.process(sanction_id=new)
    async with database() as session:
        assert (await session.get(GroupSanction, mid)).status == "ACTIVE"
        replacement = await session.get(GroupSanction, new)
        assert (
            replacement.status
            == {"reject": "FAILED", "false": "FAILED", "timeout": "UNCERTAIN", "retry": "PENDING"}[
                error
            ]
        )
        # Make the old automatic release due. Unknown/in-flight BAN must fence it.
        original = await session.get(GroupSanction, mid)
        original.until_date = now() - timedelta(seconds=1)
        release = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == mid, GroupSanctionJob.operation == "RELEASE"
            )
        )
        release.available_at = now() - timedelta(seconds=1)
        await session.commit()
    if error in {"timeout", "retry"}:
        assert await worker.claim(mid) is None
        async with database() as session:
            with pytest.raises(DomainError, match="uncertain|busy"):
                await GroupModerationService(session, bot).revoke(
                    CHAT, 42, mid, key="release-during-ban"
                )
    else:
        assert await worker.claim(mid) is not None


async def test_retry_after_replacement_can_complete_once(database):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    new = await request(database, bot, "BAN", "replacement-retry")
    bot.error = TelegramRetryAfter(
        method=BanChatMember(chat_id=CHAT, user_id=51), message="wait", retry_after=1
    )
    await worker.process(sanction_id=new)
    bot.error = None
    async with database() as session:
        job = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == new, GroupSanctionJob.operation == "APPLY"
            )
        )
        job.available_at = now() - timedelta(seconds=1)
        await session.commit()
    await worker.process(sanction_id=new)
    count = len(bot.calls)
    await worker.process(sanction_id=new)
    assert len(bot.calls) == count
    async with database() as session:
        assert (await session.get(GroupSanction, mid)).status == "COMPLETED"
        assert (await session.get(GroupSanction, new)).status == "ACTIVE"


async def test_release_already_claimed_blocks_replacement(database):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    async with database() as session:
        await GroupModerationService(session, bot).revoke(CHAT, 42, mid, key="manual-unmute")
    assert await worker.claim(mid) is not None
    with pytest.raises(DomainError, match="conflict|busy"):
        await request(database, bot, "BAN", "ban-during-unmute")


async def test_external_ban_during_replacement_is_not_claimed_as_owned(database):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    new = await request(database, bot, "BAN", "external-ban-race")
    bot.members[CHAT, 51] = bot.peer(51, "kicked", until_date=None)
    await worker.process(sanction_id=new)
    assert len(bot.calls) == 1
    async with database() as session:
        replacement = await session.get(GroupSanction, new)
        assert replacement.status == "CANCELLED" and not replacement.owned
        assert (await session.get(GroupSanction, mid)).status == "ACTIVE"


async def test_unowned_mute_cannot_be_used_as_replacement_authority(database):
    bot = NativeBot()
    mid, _ = await muted(database, bot)
    async with database() as session:
        (await session.get(GroupSanction, mid)).owned = False
        await session.commit()
    with pytest.raises(DomainError, match="conflict"):
        await request(database, bot, "BAN", "unowned-mute-ban")
    assert len(bot.calls) == 1


async def test_repeated_kick_does_not_ban_departed_member_again(username_journey):
    journey, native = username_journey
    await journey.send("/kick 51", actor=42, chat=CHAT)
    count = len(native.calls)
    assert count == 2 and native.members[CHAT, 51].status == "left"
    await journey.send("/kick 51", actor=42, chat=CHAT)
    assert len(native.calls) == count
    response = sent(journey)[-1]
    assert t("lm.diagnostic.not_in_group.title") in response.text
    assert response.ephemeral_message_parameters.receiver_user_id == 42


async def test_departure_between_kick_request_and_execution_is_private_failure(database):
    bot = NativeBot()
    await enabled(database, bot)
    sid = await request(database, bot, "KICK", "left-before-execution")
    bot.members[CHAT, 51] = bot.peer(51, "left")
    await LocalModerationExecutor(database, bot).process(sanction_id=sid)
    assert not bot.calls
    async with database() as session:
        assert (await session.get(GroupSanction, sid)).status == "CANCELLED"


@pytest.mark.parametrize("path", ["policy", "deadline", "group"])
async def test_all_pre_send_cancellations_cancel_unused_release(database, path):
    from app.models import GroupModerationSettings, ManagedGroup

    bot = NativeBot()
    await enabled(database, bot)
    sid = await request(database, bot, "MUTE", "cancel-before-send", now() + timedelta(days=1))
    async with database() as session:
        if path == "policy":
            (await session.get(GroupModerationSettings, CHAT)).mutes_enabled = False
        elif path == "deadline":
            (await session.get(GroupSanction, sid)).until_date = now() + timedelta(seconds=20)
        else:
            (await session.get(ManagedGroup, CHAT)).enabled = False
        await session.commit()
    await LocalModerationExecutor(database, bot).process(sanction_id=sid)
    async with database() as session:
        jobs = list(
            await session.scalars(
                select(GroupSanctionJob).where(GroupSanctionJob.sanction_id == sid)
            )
        )
        assert len(jobs) == 2 and all(job.status == "CANCELLED" for job in jobs)
        release = next(job for job in jobs if job.operation == "RELEASE")
        assert release.result == "NO_CONFIRMED_RESTRICTION"
    assert not bot.calls


async def test_foreign_or_changed_mute_is_not_overwritten(database):
    bot = NativeBot()
    mid, worker = await muted(database, bot)
    new = await request(database, bot, "KICK", "changed-mute")
    bot.members[CHAT, 51].until_date += timedelta(hours=2)
    await worker.process(sanction_id=new)
    assert len(bot.calls) == 1
    async with database() as session:
        assert (await session.get(GroupSanction, mid)).status == "ACTIVE"
        assert (await session.get(GroupSanction, new)).status == "CANCELLED"
        assert (
            await diagnose_state(GroupModerationService(session, bot), CHAT, 42, 51, "/kick")
            == "restriction"
        )


async def test_postgres_two_administrators_cannot_replace_same_mute_twice(postgres_contract):
    factory, _ = postgres_contract
    bot = NativeBot()
    await muted(factory, bot)

    async def run(kind):
        try:
            return await request(factory, bot, kind, "parallel-" + kind)
        except DomainError:
            return None

    results = await asyncio.gather(run("BAN"), run("KICK"))
    assert sum(result is not None for result in results) == 1
    assert len(bot.calls) == 1


async def test_old_unmute_button_and_repeated_ban_cannot_change_new_ban(username_journey, database):
    journey, native = username_journey
    await journey.send("/mute 51 1d", actor=42, chat=CHAT)
    async with database() as session:
        mid = await session.scalar(select(GroupSanction.id).where(GroupSanction.kind == "MUTE"))
        message_id = await session.scalar(
            select(GroupModerationDraft.panel_message_id).where(
                GroupModerationDraft.sanction_id == mid
            )
        )
    await journey.send("/ban 51", actor=42, chat=CHAT)
    assert native.members[CHAT, 51].status == "kicked"
    edits = [
        m
        for m in journey.transport.calls
        if isinstance(m, EditMessageText) and m.message_id == message_id
    ]
    assert edits and t("lm.control.replaced", kind="BAN") in edits[-1].text
    assert edits[-1].reply_markup is None
    count = len(native.calls)
    await click(
        journey, ModerationControl(action="release", sanction=mid).pack(), message_id=message_id
    )
    await journey.send("/ban 51", actor=42, chat=CHAT)
    assert len(native.calls) == count and native.members[CHAT, 51].status == "kicked"
    assert sent(journey)[-1].ephemeral_message_parameters.receiver_user_id == 42

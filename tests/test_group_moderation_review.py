"""Adversarial review regressions, separate from the initial happy-path contracts."""

import asyncio
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import BanChatMember
from sqlalchemy import select, update
from test_group_moderation_foundation import CHAT, NativeBot, setup_groups, submit
from test_integration_contracts import postgres_contract as postgres_contract
from test_mtproto_relay import prepare

from app.bot.group_runtime import _attempt_ban
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.scam_unban import process_scam_unban
from app.errors import DomainError
from app.group_services import GroupService
from app.models import AuditEvent, GroupSanction, GroupSanctionJob, ManagedGroup, now
from app.moderation_services import GroupModerationService
from app.services import Service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
async def test_review_local_apply_cannot_weaken_active_scam(database, settings, kind):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot, kind=kind)
    async with database() as session:
        await Service(settings, session).add_scam(900, "51")
    await LocalModerationExecutor(database, bot).process()
    assert bot.calls == []
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "CANCELLED"


async def test_review_scam_ban_strengthens_existing_temporary_ban():
    bot = NativeBot()
    bot.members[CHAT, 51] = bot.peer(51, "kicked", until_date=now() + timedelta(hours=1))
    success, result = await _attempt_ban(bot, SimpleNamespace(chat_id=CHAT, telegram_id=51), 8)
    assert success and result == "BANNED"
    assert len(bot.calls) == 1
    assert bot.members[CHAT, 51].until_date is None


async def test_review_staff_response_must_confirm_permanent_scam_ban(database):
    settings, _, relay, client = await prepare(database)
    bot = NativeBot()
    async with database() as session:
        service = GroupService(settings, session)
        job = (await service.claim_bans())[0]
        bot.members[job.chat_id, job.telegram_id] = bot.peer(
            job.telegram_id, "kicked", until_date=now() + timedelta(hours=1)
        )
        success, result = await relay.attempt_ban(bot, service, job, 8)
    assert success and result == "BANNED"
    assert bot.members[job.chat_id, job.telegram_id].until_date is None
    assert any(hasattr(request, "message") for request in client.calls)
    assert len(bot.calls) == 1


async def test_review_revoke_cannot_reset_claimed_automatic_release(database, monkeypatch):
    from app.bot import local_moderation

    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    expired = sanction.until_date + timedelta(seconds=1)
    monkeypatch.setattr(local_moderation, "now", lambda: expired)
    claim = await worker.claim()
    assert claim is not None
    async with database() as session:
        with pytest.raises(DomainError, match="busy"):
            await GroupModerationService(session, bot).revoke(
                CHAT, 42, sanction.id, key="review-revoke"
            )
        job = await session.get(GroupSanctionJob, claim[0])
        assert job.status == "PROCESSING" and job.automatic and job.lease_token == claim[1]
    await worker.execute(*claim)
    assert [call[0] for call in bot.calls] == ["ban", "unban"]


async def test_review_timed_ban_cannot_become_permanent_in_basic_group(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    async with database() as session:
        group = await session.get(ManagedGroup, CHAT)
        group.chat_type = "group"
        await session.commit()
    with pytest.raises(DomainError, match="supergroup_required"):
        await submit(database, bot)
    assert bot.calls == []


async def test_review_worker_rechecks_group_type(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    await submit(database, bot)
    async with database() as session:
        group = await session.get(ManagedGroup, CHAT)
        group.chat_type = "group"
        await session.commit()
    await LocalModerationExecutor(database, bot).process()
    assert bot.calls == []


async def test_review_worker_rechecks_deadline_after_slow_preflight(database, monkeypatch):
    from app.bot import local_moderation

    bot = NativeBot()
    await setup_groups(database, bot)
    clock = now()
    await submit(database, bot, until=clock + timedelta(seconds=75))
    worker = LocalModerationExecutor(database, bot)
    target = worker.authorization.target

    async def delayed_target(chat, user):
        member = await target(chat, user)
        # 56 seconds of preflight/SQL contention: lease valid, Telegram expiry < 30 seconds.
        monkeypatch.setattr(local_moderation, "now", lambda: clock + timedelta(seconds=56))
        return member

    monkeypatch.setattr(worker.authorization, "target", delayed_target)
    await worker.process()
    assert bot.calls == []


async def duplicate_execution_contract(sessions, monkeypatch):
    from app.bot import local_moderation

    # Separate processes do not share asyncio locks; only SQL fencing remains.
    monkeypatch.setattr(local_moderation, "user_moderation_lock", lambda _: nullcontext())
    bot = NativeBot()
    await setup_groups(sessions, bot)
    await submit(sessions, bot)
    first, second = LocalModerationExecutor(sessions, bot), LocalModerationExecutor(sessions, bot)
    claim = await first.claim()
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_write(**kwargs):
        bot.calls.append(("ban", kwargs))
        entered.set()
        await release.wait()
        return True

    bot.ban_chat_member = slow_write
    first_task = asyncio.create_task(first.execute(*claim))
    second_task = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second_task = asyncio.create_task(second.execute(*claim))
        await asyncio.wait_for(asyncio.shield(second_task), 1)
        assert len(bot.calls) == 1
    finally:
        release.set()
        await asyncio.gather(first_task, *([second_task] if second_task else []))


async def test_review_duplicate_execution_without_process_lock(database, monkeypatch):
    await duplicate_execution_contract(database, monkeypatch)


async def test_review_postgres_duplicate_execution_without_process_lock(
    postgres_contract, monkeypatch
):
    sessions, _ = postgres_contract
    await duplicate_execution_contract(sessions, monkeypatch)


async def test_review_crash_after_api_acceptance_is_not_replayed(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    original = bot.ban_chat_member

    async def accepted_then_crash(**kwargs):
        await original(**kwargs)
        raise asyncio.CancelledError

    bot.ban_chat_member = accepted_then_crash
    worker = LocalModerationExecutor(database, bot)
    with pytest.raises(asyncio.CancelledError):
        await worker.process()
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "APPLY"
            )
            .values(lease_until=now() - timedelta(seconds=1))
        )
        await session.commit()
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "UNCERTAIN"
        job = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "APPLY"
            )
        )
        assert job.result == "LEASE_EXPIRED"
    assert len(bot.calls) == 1 and bot.members[CHAT, 51].status == "kicked"


async def test_review_automatic_flag_cannot_bypass_actor_rights_for_apply(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "APPLY"
            )
            .values(automatic=True)
        )
        await session.commit()
    bot.members[CHAT, 42] = bot.peer(42)
    await LocalModerationExecutor(database, bot).process()
    assert bot.calls == []


async def test_review_early_expiry_job_is_deferred_to_real_deadline(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "RELEASE"
            )
            .values(available_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    assert await worker.claim() is None
    assert len(bot.calls) == 1


async def test_review_cancellation_refreshes_concurrently_completed_apply(database, monkeypatch):
    from app.bot import local_moderation

    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    original_lock = local_moderation.serialize_group

    async def concurrent_completion(session, chat):
        async with database() as other:
            # Another worker committed its successful Telegram response after our read.
            row = await other.get(GroupSanction, sanction.id)
            row.status, row.owned = "ACTIVE", True
            await other.execute(
                update(GroupSanctionJob)
                .where(
                    GroupSanctionJob.sanction_id == sanction.id,
                    GroupSanctionJob.operation == "APPLY",
                )
                .values(status="SUCCEEDED")
            )
            await other.commit()
        await original_lock(session, chat)

    async with database() as session:
        release = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "RELEASE"
            )
        )
        release_id = release.id
    monkeypatch.setattr(local_moderation, "serialize_group", concurrent_completion)
    await LocalModerationExecutor(database, bot)._cancel_unclaimed(release_id, "GROUP_UNAVAILABLE")
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "ACTIVE"


async def test_review_read_retry_gets_fresh_execution_fence(database, monkeypatch):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    original = bot.get_chat_member
    failed = False

    async def read_failure(chat_id, user_id):
        nonlocal failed
        if not failed:
            failed = True
            raise TimeoutError
        return await original(chat_id, user_id)

    monkeypatch.setattr(bot, "get_chat_member", read_failure)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    assert bot.calls == []
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "APPLY"
            )
            .values(available_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    await LocalModerationExecutor(database, bot).process()
    assert len(bot.calls) == 1


async def test_review_api_reason_is_auditable_and_redacted(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    await submit(database, bot)
    secret = "123456:TEST_ONLY_ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    bot.error = TelegramForbiddenError(
        method=BanChatMember(chat_id=CHAT, user_id=51),
        message=f"Not enough rights\nhttps://example.test/{secret} {secret}",
    )
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "group_moderation_attempt")
        )
        reason = audit.details["reason"]
        assert "Not enough rights" in reason
        assert secret not in reason and "https://" not in reason and "\n" not in reason


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
async def test_review_global_scam_unban_cannot_override_local_authority(database, settings, kind):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot, kind=kind)
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        record = await Service(settings, session).add_scam(900, "51")
        record_id = record.id
    # SCAM protection superseded the local restriction with an indefinite ban.
    await bot.ban_chat_member(chat_id=CHAT, user_id=51)
    previous_calls = len(bot.calls)
    async with database() as session:
        record, summary = await process_scam_unban(bot, Service(settings, session), record_id, 900)
        assert record.status == "REMOVED"
        assert (await session.get(GroupSanction, sanction.id)).status == "ACTIVE"
        protected = next(row for row in summary["groups"] if row["chat_id"] == CHAT)
        assert protected["result"] == "LOCAL_SANCTION_PROTECTED" and not protected["success"]
    assert len(bot.calls) == previous_calls
    assert bot.members[CHAT, 51].status == "kicked"


async def test_review_staff_global_unban_cannot_override_local_authority(database):
    settings, record_id, relay, client = await prepare(database)
    async with database() as session:
        await Service(settings, session).remove_scam(900, "22", "Reviewed by administrator")
        session.add(
            GroupSanction(
                chat_id=-1000,
                telegram_id=22,
                kind="BAN",
                actor_id=42,
                reason="Local rule",
                status="ACTIVE",
                owned=True,
                policy_version=1,
                request_key="review-existing-local",
            )
        )
        await session.commit()
    assert not await relay.dispatch_unban(900, record_id)
    assert client.calls == []

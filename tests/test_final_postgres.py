"""Real PostgreSQL revocation and identity contracts in disposable isolated schemas."""

import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest
from test_ban_failure_notices import AlertBot
from test_integration_contracts import postgres_contract as postgres_fixture

from app.admin_services import AdminService
from app.bot.group_runtime import process_scam_bans
from app.bot.moderation_notices import notify_ban_failure
from app.errors import DomainError
from app.group_services import GroupService
from app.scam_management import ScamManagement
from app.services import Service

postgres_contract = postgres_fixture
pytestmark = pytest.mark.asyncio


async def test_postgres_bigint_group_presence_retries_and_alerts_once(postgres_contract):
    factory, settings = postgres_contract
    settings = settings.model_copy(update={"group_owner_id": 900})
    chat_id = -1000000000001
    async with factory() as session:
        await GroupService(settings, session).register_group(900, chat_id, "Test group", True)
        record_id = (await Service(settings, session).add_scam(900, "22")).id
    bot = AlertBot({chat_id: TelegramBadRequest})
    await process_scam_bans(bot, settings, factory, record_id)
    for _ in range(2):
        async with factory() as session:
            await GroupService(settings, session).check_member(chat_id, 22, fresh_join=True)
        await process_scam_bans(bot, settings, factory, record_id)
    assert len(bot.notices) == 1 and bot.notices[0][0] == 900
    bot.failures.clear()
    async with factory() as session:
        await GroupService(settings, session).check_member(chat_id, 22, fresh_join=True)
    assert (await process_scam_bans(bot, settings, factory, record_id)).succeeded == 1


async def test_postgres_concurrent_failure_alerts_are_sent_only_once(postgres_contract):
    factory, settings = postgres_contract
    settings = settings.model_copy(update={"group_owner_id": 900})
    chat_id = -1000000000002
    async with factory() as session:
        await GroupService(settings, session).register_group(900, chat_id, "Test group", True)
        await Service(settings, session).add_scam(900, "22")
    bot = AlertBot()

    async def alert():
        async with factory() as session:
            await notify_ban_failure(
                bot,
                GroupService(settings, session),
                chat_id,
                22,
                present=True,
            )

    await asyncio.gather(alert(), alert())
    assert len(bot.notices) == 1 and bot.notices[0][0] == 900


async def test_postgres_revocation_wins_before_waiting_privileged_write(postgres_contract):
    factory, settings = postgres_contract
    settings = settings.model_copy(update={"group_owner_id": 900})
    async with factory() as seed:
        core = Service(settings, seed)
        await core.access.change(900, "902", True, "a" * 32)
        await core.resolve("42")
    owner_locked, actor_waiting, allow_commit = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def revoke():
        async with factory() as session:
            access = AdminService(settings, session)
            original = access.repo.lock_identity_metadata

            async def hold():
                await original()
                owner_locked.set()
                await allow_commit.wait()

            access.repo.lock_identity_metadata = hold
            await access.change(900, "902", False, "b" * 32)

    async def mutate():
        async with factory() as session:
            core = Service(settings, session)
            assert await core.is_admin(902)
            original = core.repo.lock_identity_metadata

            async def waiting():
                actor_waiting.set()
                await original()

            core.repo.lock_identity_metadata = waiting
            with pytest.raises(DomainError, match="forbidden"):
                await core.add_scam(902, "42")

    owner = asyncio.create_task(revoke())
    actor = None
    try:
        await asyncio.wait_for(owner_locked.wait(), 20)
        actor = asyncio.create_task(mutate())
        await asyncio.wait_for(actor_waiting.wait(), 20)
        allow_commit.set()
        await asyncio.wait_for(asyncio.gather(owner, actor), 30)
        async with factory() as session:
            assert (await Service(settings, session).profile("42"))["scam"] is None
    finally:
        allow_commit.set()
        for task in (owner, actor):
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*[task for task in (owner, actor) if task], return_exceptions=True)


async def test_postgres_manual_binding_refuses_different_username_owner(postgres_contract):
    factory, settings = postgres_contract
    async with factory() as session:
        core = Service(settings, session)
        record = await core.add_scam(900, "@historical")
        record_id = record.id
        await core.observe(41, "historical", "Observed owner")
        with pytest.raises(DomainError, match="sm_conflict"):
            await ScamManagement(core).supplement(900, record_id, "id", "42", "c" * 32)
        result = await ScamManagement(core).supplement(900, record_id, "id", "41", "d" * 32)
        assert result.target.telegram_id == 41
        assert (await core.profile("@historical"))["scam"].id == record_id

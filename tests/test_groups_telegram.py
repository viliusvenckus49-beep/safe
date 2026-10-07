"""Managed-group Telegram routing and durable delivery without real Telegram actions."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    Update,
    User,
)
from sqlalchemy import select
from test_telegram import Journey, Transport

from app.bot.group_runtime import process_group_jobs
from app.bot.groups import register_group_handlers
from app.bot.handlers import create_router
from app.group_services import GroupService
from app.models import BanAction, ManagedGroup, ObservedMember
from app.services import Service


@pytest.mark.asyncio
async def test_trusted_bot_promotion_required(database, settings):
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dp = Dispatcher(storage=MemoryStorage())
    from aiogram import Router

    router = Router()
    register_group_handlers(router, settings, database)
    dp.include_router(router)
    robot = User(id=123456, is_bot=True, first_name="Bot")
    for actor in (1, 900):
        event = ChatMemberUpdated(
            chat=Chat(id=-1001, type="supergroup", title="Group"),
            from_user=User(id=actor, is_bot=False, first_name="Admin"),
            date=datetime.now(UTC),
            old_chat_member=ChatMemberMember(user=robot),
            new_chat_member=ChatMemberAdministrator(
                user=robot,
                can_be_edited=True,
                is_anonymous=False,
                can_manage_chat=True,
                can_delete_messages=True,
                can_manage_video_chats=False,
                can_restrict_members=True,
                can_promote_members=False,
                can_change_info=True,
                can_invite_users=True,
                can_post_stories=False,
                can_edit_stories=False,
                can_delete_stories=False,
                can_send_welcome_messages=False,
            ),
        )
        await dp.feed_update(bot, Update(update_id=actor, my_chat_member=event))
        async with database() as session:
            group = await session.get(ManagedGroup, -1001)
            assert (group is not None) == (actor == 900)
    await dp.storage.close()
    await bot.session.close()


@pytest.mark.asyncio
async def test_group_plain_message_observed_without_response(database, settings):
    async with database() as session:
        await GroupService(settings, session).register_group(900, -1001, "Group", True)
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(create_router(settings, database))
    journey = Journey(bot, dp, transport)
    await journey.send("ordinary conversation", actor=2, chat=-1001)
    assert not transport.calls
    async with database() as session:
        assert await session.get(ObservedMember, (-1001, 2)) is not None
    await dp.storage.close()
    await bot.session.close()


class WorkerBot:
    def __init__(self):
        self.bans = []
        self.messages = []

    async def ban_chat_member(self, *, chat_id, user_id):
        self.bans.append((chat_id, user_id))
        return True

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


@pytest.mark.asyncio
async def test_preemptive_ban_without_group_membership(database, settings):
    async with database() as session:
        core = Service(settings, session)
        await core.observe(22, "example", "Example Name")
        await GroupService(settings, session).register_group(900, -1001, "Group", True)
        await core.add_scam(900, "22", "Confirmed fraudulent activity")
    bot = WorkerBot()
    assert await process_group_jobs(bot, settings, database) == 1
    assert bot.bans == [(-1001, 22)]
    async with database() as session:
        job = await session.scalar(select(BanAction))
        assert job.status == "SUCCEEDED"
    assert await process_group_jobs(bot, settings, database) == 0


@pytest.mark.asyncio
async def test_recovery_only_explicit_consent(database, settings):
    async with database() as session:
        service = GroupService(settings, session)
        await service.register_group(900, -1001, "<Group>", True)
        for actor in (1, 2):
            await service.mark_private_contact(actor)
            await service.observe_member(-1001, actor, None, "Member")
        await service.subscribe(1, -1001, True)
        await service.queue_recovery(900, -1001, "https://t.me/+Abc123", "nonce-123456")
    bot = WorkerBot()
    assert await process_group_jobs(bot, settings, database) == 1
    assert len(bot.messages) == 1
    assert bot.messages[0][0] == 1
    assert "&lt;Group&gt;" in bot.messages[0][1]
    assert await process_group_jobs(bot, settings, database) == 0


@pytest.mark.asyncio
async def test_worker_honors_telegram_retry_after(database, settings):
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import BanChatMember

    from app.models import now

    async with database() as session:
        core = Service(settings, session)
        await core.observe(22, "example", "Example")
        await GroupService(settings, session).register_group(900, -1001, "Group", True)
        await core.add_scam(900, "22", "Confirmed fraudulent activity")

    class FloodBot(WorkerBot):
        async def ban_chat_member(self, *, chat_id, user_id):
            raise TelegramRetryAfter(
                method=BanChatMember(chat_id=chat_id, user_id=user_id),
                message="Flood control",
                retry_after=600,
            )

    assert await process_group_jobs(FloodBot(), settings, database) == 1
    async with database() as session:
        job = await session.scalar(select(BanAction))
        assert job.status == "FAILED"
        assert job.result_type == "TelegramRetryAfter"
        scheduled = job.next_attempt_at.replace(tzinfo=UTC)
        assert (scheduled - now()).total_seconds() > 590


@pytest.mark.asyncio
async def test_worker_rechecks_scam_after_claim(database, settings, monkeypatch):
    from sqlalchemy import update

    from app.models import ScamRecord

    async with database() as session:
        core = Service(settings, session)
        await core.observe(22, "example", "Example")
        await GroupService(settings, session).register_group(900, -1001, "Group", True)
        await core.add_scam(900, "22", "Confirmed fraudulent activity")
    original = GroupService.claim_bans

    async def claim_then_remove(self, limit=20):
        jobs = await original(self, limit)
        async with database() as other:
            await other.execute(update(ScamRecord).values(status="REMOVED"))
            await other.commit()
        return jobs

    monkeypatch.setattr(GroupService, "claim_bans", claim_then_remove)
    bot = WorkerBot()
    assert await process_group_jobs(bot, settings, database) == 0
    assert not bot.bans
    async with database() as session:
        assert (await session.scalar(select(BanAction))).status == "OBSOLETE"


@pytest.mark.asyncio
async def test_worker_rechecks_consent_after_claim(database, settings, monkeypatch):
    from app.models import RecoveryDelivery

    async with database() as session:
        service = GroupService(settings, session)
        await service.register_group(900, -1001, "Group", True)
        await service.mark_private_contact(1)
        await service.observe_member(-1001, 1, None, "Member")
        await service.subscribe(1, -1001, True)
        await service.queue_recovery(900, -1001, "https://t.me/+Abc123", "nonce-123456")
    original = GroupService.claim_deliveries

    async def claim_then_unsubscribe(self, limit=20):
        jobs = await original(self, limit)
        async with database() as other:
            await GroupService(settings, other).subscribe(1, -1001, False)
        return jobs

    monkeypatch.setattr(GroupService, "claim_deliveries", claim_then_unsubscribe)
    bot = WorkerBot()
    assert await process_group_jobs(bot, settings, database) == 0
    assert not bot.messages
    async with database() as session:
        assert (await session.scalar(select(RecoveryDelivery))).status == "OBSOLETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [False, True])
async def test_owner_group_start_opens_home_and_private_groups_remain(database, settings, approved):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app import presentation as presentation
    from app.bot.callbacks import Action
    from app.bot.group_keyboards import GroupAction

    async with database() as session:
        groups = GroupService(settings, session)
        if approved:
            await groups.register_group(900, -1001, "Group", True)
        else:
            await groups.stage_group(900, -1001, "Group")
        await Service(settings, session).set_language(900, "lt")
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status="administrator", can_restrict_members=True)
    )
    journey = Journey(bot, dispatcher, transport)
    try:
        await journey.send("/start", actor=900, chat=-1001)
        assert presentation.home() in journey.text()
        buttons = transport.calls[-1].reply_markup.inline_keyboard
        assert any(
            button.callback_data == Action(name="lookup").pack()
            for row in buttons
            for button in row
        )
        await journey.send("/groups", actor=900)
        buttons = transport.calls[-1].reply_markup.inline_keyboard
        assert any(
            button.callback_data == GroupAction(action="group", chat_id=-1001).pack()
            for row in buttons
            for button in row
        )
        async with database() as session:
            group = await session.get(ManagedGroup, -1001)
            assert group.approved is approved
            assert group.enabled is approved
    finally:
        await dispatcher.storage.close()
        await bot.session.close()

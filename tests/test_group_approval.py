"""Closed enrollment requires configured owner and fresh Telegram permissions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from test_telegram import Journey, Transport

from app.bot.group_authorization import verify_group
from app.bot.group_keyboards import GroupAction
from app.bot.handlers import create_router
from app.group_services import GroupService
from app.models import ManagedGroup
from app.services import DomainError


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", [1, 901])
async def test_approval_server_side_owner_guard_before_telegram(settings, actor):
    settings.admin_ids = "900,901"
    settings.group_owner_id = 900
    bot = SimpleNamespace(get_chat=AsyncMock())
    with pytest.raises(DomainError, match="forbidden"):
        await verify_group(bot, settings, actor, -100)
    bot.get_chat.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner_status,bot_status,restrict,allowed",
    [
        ("creator", "administrator", True, True),
        ("administrator", "administrator", True, True),
        ("member", "administrator", True, False),
        ("creator", "member", False, False),
        ("creator", "administrator", False, False),
    ],
)
async def test_fresh_owner_and_bot_rights(settings, owner_status, bot_status, restrict, allowed):
    bot = SimpleNamespace(
        id=123456,
        get_chat=AsyncMock(return_value=SimpleNamespace(id=-100, type="supergroup", title="Group")),
        get_chat_member=AsyncMock(
            side_effect=[
                SimpleNamespace(status=owner_status),
                SimpleNamespace(status=bot_status, can_restrict_members=restrict),
            ]
        ),
    )
    if allowed:
        assert (await verify_group(bot, settings, 900, -100)).id == -100
    else:
        with pytest.raises(DomainError):
            await verify_group(bot, settings, 900, -100)
    assert bot.get_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_pending_owner_approval_and_callback_bypass(database, settings, monkeypatch):
    settings.admin_ids = "900,901"
    settings.group_owner_id = 900
    async with database() as session:
        await GroupService(settings, session).stage_group(900, -100, "Group")
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(create_router(settings, database))
    journey = Journey(bot, dp, transport)
    verify = AsyncMock(return_value=SimpleNamespace(id=-100, type="supergroup", title="Group"))
    monkeypatch.setattr("app.bot.groups.verify_group", verify)
    action = GroupAction(action="approve", chat_id=-100).pack()
    for actor in [1, 901]:
        await journey.click(action, actor=actor)
        verify.assert_not_awaited()
        async with database() as session:
            assert (await session.get(ManagedGroup, -100)).approved is False
    await journey.click(action, actor=900)
    async with database() as session:
        group = await session.get(ManagedGroup, -100)
        assert group.approved and group.enabled
    await journey.click(action, actor=900)
    assert verify.await_count == 2
    await dp.storage.close()
    await bot.session.close()

"""Owner withdrawal, stale UI, durable history and already-claimed outbox protection."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, LeaveChat
from aiogram.types import Chat, ChatMemberMember, ChatMemberUpdated, Update, User
from sqlalchemy import func, select
from test_groups_telegram import WorkerBot
from test_telegram import Journey, Transport

from app.bot.group_keyboards import GroupAction, group, remove_confirmation
from app.bot.group_runtime import process_group_jobs
from app.bot.handlers import create_router
from app.group_services import GroupService
from app.i18n import t, use_language
from app.models import (
    AuditEvent,
    BanAction,
    ManagedGroup,
    ObservedMember,
    RecoveryCampaign,
    RecoveryDelivery,
    RecoverySubscription,
    ScamRecord,
)
from app.services import DomainError, Service

CHAT = -1000000000001
OTHER = -1000000000002
pytestmark = pytest.mark.asyncio


async def seed(factory, settings):
    async with factory() as session:
        groups = GroupService(settings, session)
        for chat in (CHAT, OTHER):
            await groups.register_group(900, chat, "<Group>", True)
            await groups.mark_private_contact(21)
            await groups.observe_member(chat, 21, "member_name", "Member")
            await groups.subscribe(21, chat, True)
            await groups.queue_recovery(900, chat, "https://t.me/+Invite123", f"campaign:{chat}")
        await Service(settings, session).add_scam(900, "22")


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "PROCESSING", "SUCCEEDED"])
async def test_remove_hides_group_cancels_work_and_preserves_history(database, settings, status):
    await seed(database, settings)
    async with database() as session:
        groups = GroupService(settings, session)
        ban = await session.scalar(select(BanAction).where(BanAction.chat_id == CHAT))
        campaign = await session.scalar(
            select(RecoveryCampaign).where(RecoveryCampaign.chat_id == CHAT)
        )
        delivery = await session.scalar(
            select(RecoveryDelivery).where(RecoveryDelivery.campaign_id == campaign.id)
        )
        ban.status = delivery.status = status
        await session.commit()
        await groups.remove_group(900, CHAT)
        removed = await session.get(ManagedGroup, CHAT, populate_existing=True)
        assert not removed.approved and not removed.enabled and not removed.can_restrict_members
        assert [g.chat_id for g in await groups.groups(900)] == [OTHER]
        assert [g.chat_id for g in await groups.groups_for_subscription(21)] == [OTHER]
        assert not (await session.get(RecoverySubscription, (CHAT, 21))).consent
        assert await session.get(ObservedMember, (CHAT, 21)) is not None
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 1
        assert await session.scalar(select(func.count()).select_from(ManagedGroup)) == 2
        assert await session.scalar(select(func.count()).select_from(RecoveryCampaign)) == 2
        await session.refresh(ban)
        await session.refresh(delivery)
        expected = "SUCCEEDED" if status == "SUCCEEDED" else "OBSOLETE"
        assert ban.status == delivery.status == expected
        assert (
            await session.scalar(select(AuditEvent).where(AuditEvent.action == "group_removed"))
        ).details["chat_id"] == CHAT
        other_ban = await session.scalar(select(BanAction).where(BanAction.chat_id == OTHER))
        assert other_ban.status == "PENDING"
        await Service(settings, session).add_scam(900, "23")
        assert (
            await session.scalar(
                select(func.count()).select_from(BanAction).where(BanAction.chat_id == CHAT)
            )
            == 1
        )


async def test_removed_group_cannot_be_reactivated_or_recovered(database, settings):
    await seed(database, settings)
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.remove_group(900, CHAT)
        await groups.stage_group(900, CHAT, "Rejoined")
        await groups.note_group_permissions(CHAT, True)
        await groups.observe_member(CHAT, 24, "new_member", "New member")
        await groups.check_member(CHAT, 22, fresh_join=True)
        assert await session.get(ObservedMember, (CHAT, 24)) is None
        for operation in (
            lambda: groups.register_group(900, CHAT, "Rejoined", True),
            lambda: groups.remove_group(900, CHAT),
            lambda: groups.subscribe(21, CHAT, True),
            lambda: groups.export_members(900, CHAT),
            lambda: groups.prepare_recovery(900, CHAT, "https://t.me/+Invite123", "new"),
            lambda: groups.queue_recovery(900, CHAT, "https://t.me/+Invite123", "new"),
        ):
            with pytest.raises(DomainError):
                await operation()
        group = await session.get(ManagedGroup, CHAT, populate_existing=True)
        assert not group.approved and not group.enabled and not group.can_restrict_members


@pytest.mark.parametrize("approved", [True, False])
async def test_only_owner_can_remove_approved_or_staged_group(database, settings, approved):
    settings = settings.model_copy(update={"admin_ids": "900,901", "group_owner_id": 900})
    async with database() as session:
        groups = GroupService(settings, session)
        if approved:
            await groups.register_group(900, CHAT, "Group", True)
        else:
            await groups.stage_group(900, CHAT, "Group")
        for actor in (1, 901):
            with pytest.raises(DomainError, match="forbidden"):
                await groups.remove_group(actor, CHAT)
        assert not await groups.is_removed_group(CHAT)
        await groups.remove_group(900, CHAT)
        assert await groups.groups(900) == []


async def test_removal_invalidates_cached_claims_and_stale_worker_results(database, settings):
    await seed(database, settings)
    async with database() as worker:
        groups = GroupService(settings, worker)
        bans = await groups.claim_bans()
        deliveries = await groups.claim_deliveries()
        removed_ban = next(b for b in bans if b.chat_id == CHAT)
        removed_delivery = next(d for d in deliveries if d.campaign.chat_id == CHAT)
        async with database() as owner:
            await GroupService(settings, owner).remove_group(900, CHAT)
        # Cached PROCESSING objects must not resurrect jobs invalidated by another session.
        await groups.finish_ban(removed_ban.id, False, "TelegramBadRequest")
        await groups.finish_delivery(removed_delivery.id, True, "SENT")
        assert removed_ban.status == removed_delivery.status == "OBSOLETE"
        assert not await groups.ban_eligible(removed_ban.id)
        assert not await groups.delivery_eligible(removed_delivery.id)


async def test_worker_revalidates_group_removed_after_claim(database, settings, monkeypatch):
    await seed(database, settings)
    original = GroupService.claim_bans

    async def claimed_then_removed(self, *args, **kwargs):
        claims = await original(self, *args, **kwargs)
        async with database() as owner:
            await GroupService(settings, owner).remove_group(900, CHAT)
        return claims

    monkeypatch.setattr(GroupService, "claim_bans", claimed_then_removed)
    bot = WorkerBot()
    assert await process_group_jobs(bot, settings, database) == 2
    assert bot.bans == [(OTHER, 22)]
    assert len(bot.messages) == 1


@pytest_asyncio.fixture
async def removal_journey(database, settings):
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    yield Journey(bot, dispatcher, transport)
    await dispatcher.storage.close()
    await bot.session.close()


@pytest.mark.parametrize("leave_result", ["success", "false", "rejected"])
async def test_groups_remove_confirmation_and_leave_outcome(
    database, settings, removal_journey, leave_result
):
    await seed(database, settings)
    journey = removal_journey
    leave = AsyncMock(return_value=leave_result == "success")
    if leave_result == "rejected":
        leave.side_effect = TelegramBadRequest(method=LeaveChat(chat_id=CHAT), message="Rejected")
    journey.bot.leave_chat = leave
    await journey.send("/groups", actor=900)
    await journey.click(GroupAction(action="group", chat_id=CHAT).pack(), actor=900)
    assert any(
        button.callback_data == GroupAction(action="remove", chat_id=CHAT).pack()
        for row in journey.transport.calls[-1].reply_markup.inline_keyboard
        for button in row
    )
    await journey.click(GroupAction(action="remove", chat_id=CHAT).pack(), actor=900)
    nonce = (await journey.data(900))["nonce"]
    await journey.click(
        GroupAction(action="remove_confirm", chat_id=CHAT, nonce="wrong").pack(), actor=900
    )
    leave.assert_not_awaited()
    await journey.click(GroupAction(action="remove", chat_id=CHAT).pack(), actor=900)
    nonce = (await journey.data(900))["nonce"]
    confirmation = GroupAction(action="remove_confirm", chat_id=CHAT, nonce=nonce).pack()
    await journey.click(confirmation, actor=900)
    leave.assert_awaited_once_with(CHAT)
    assert "&lt;Group&gt;" in journey.text()
    assert (
        t("group.LEAVE_FAILED") in journey.text()
        if leave_result != "success"
        else (t("group.LEAVE_FAILED") not in journey.text())
    )
    assert await journey.state(900) is None
    await journey.click(confirmation, actor=900)
    leave.assert_awaited_once()
    async with database() as session:
        assert [g.chat_id for g in await GroupService(settings, session).groups(900)] == [OTHER]


async def test_removed_group_is_silent_for_messages_old_buttons_and_joins(
    database, settings, removal_journey
):
    await seed(database, settings)
    async with database() as session:
        await GroupService(settings, session).remove_group(900, CHAT)
    journey = removal_journey
    for text in ("/start", "/ask 22", "/add_sc 25", "+rep 25 comment", "ordinary message"):
        await journey.send(text, actor=900, chat=CHAT)
    await journey.send("scammer is here", actor=22, chat=CHAT)
    await journey.send("/start", actor=900, chat=CHAT, sender_chat=Chat(id=CHAT, type="supergroup"))
    assert not journey.transport.calls
    await journey.click(GroupAction(action="approve", chat_id=CHAT).pack(), actor=900, chat=CHAT)
    assert len(journey.transport.calls) == 1
    assert isinstance(journey.transport.calls[0], AnswerCallbackQuery)
    event = ChatMemberUpdated(
        chat=Chat(id=CHAT, type="supergroup", title="Group"),
        from_user=User(id=900, is_bot=False, first_name="Owner"),
        date=datetime.now(UTC),
        old_chat_member=ChatMemberMember(user=User(id=22, is_bot=False, first_name="User")),
        new_chat_member=ChatMemberMember(user=User(id=22, is_bot=False, first_name="User")),
    )
    await journey.dp.feed_update(journey.bot, Update(update_id=999, chat_member=event))
    assert len(journey.transport.calls) == 1
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 1
        assert await session.get(ObservedMember, (CHAT, 900)) is None
        assert not (await session.get(ManagedGroup, CHAT)).approved
    await journey.send("/start", actor=900)
    assert journey.text()


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_removal_buttons_localized_and_callback_length_safe(lang):
    with use_language(lang):
        for approved in (True, False):
            buttons = [b for row in group(CHAT, approved=approved).inline_keyboard for b in row]
            assert any(b.text == t("button.group_remove") for b in buttons)
        confirmation = remove_confirmation(CHAT, "a" * 16).inline_keyboard[0][0]
        assert confirmation.text == t("button.group_remove_confirm")
        assert len(confirmation.callback_data.encode()) <= 64
        assert GroupAction.unpack(confirmation.callback_data).action == "remove_confirm"
        assert "group.REMOVE_CONFIRM" not in t("group.REMOVE_CONFIRM", title="Group")

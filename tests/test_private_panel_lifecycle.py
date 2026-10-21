"""Model Telegram's real ephemeral IDs, native methods and stale ORM objects."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import (
    DeleteEphemeralMessage,
    EditEphemeralMessageReplyMarkup,
    EditEphemeralMessageText,
    EditMessageReplyMarkup,
    EditMessageText,
    SendMessage,
)
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from test_group_moderation_foundation import CHAT, NativeBot
from test_local_moderation_stage2 import confirmed, enabled, prepared
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_warning_controls import active, click, sent

from app.bot.local_admin import receipt_text
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_controls import public_receipt
from app.bot.moderation_outcomes import diagnostic
from app.i18n import t
from app.models import GroupModerationDraft, GroupSanctionJob, GroupWarningReset
from app.moderation_services import GroupModerationService

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ephemeral_journey(username_journey):
    journey, native = username_journey
    original = journey.transport.make_request
    panels = {}
    counter = 500
    journey.transport.fail_private_delete = False

    async def request(bot, method, timeout=None):
        nonlocal counter
        if isinstance(method, SendMessage) and method.ephemeral_message_parameters:
            journey.transport.calls.append(method)
            counter += 1
            actor = method.ephemeral_message_parameters.receiver_user_id
            result = Message(
                message_id=0,
                ephemeral_message_id=counter,
                receiver_user=User(id=actor, is_bot=False, first_name="Admin"),
                date=datetime.now(UTC),
                chat=Chat(id=method.chat_id, type="supergroup"),
                from_user=User(id=bot.id, is_bot=True, first_name="SAFECheck"),
                text=method.text,
                reply_markup=method.reply_markup,
            ).as_(bot)
            panels[method.chat_id, actor, counter] = result
            return result
        if isinstance(
            method,
            (EditEphemeralMessageText, EditEphemeralMessageReplyMarkup, DeleteEphemeralMessage),
        ):
            journey.transport.calls.append(method)
            key = method.chat_id, method.receiver_user_id, method.ephemeral_message_id
            if key not in panels:
                raise TelegramBadRequest(method=method, message="ephemeral message not found")
            if isinstance(method, DeleteEphemeralMessage):
                if journey.transport.fail_private_delete:
                    raise TelegramBadRequest(method=method, message="temporary cleanup rejection")
                panels.pop(key)
            else:
                updates = {"reply_markup": method.reply_markup}
                if isinstance(method, EditEphemeralMessageText):
                    updates["text"] = method.text
                panels[key] = panels[key].model_copy(update=updates).as_(bot)
            return True
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)) and method.message_id == 0:
            raise TelegramBadRequest(method=method, message="message_id=0 is not editable")
        return await original(bot, method, timeout)

    journey.transport.make_request = request
    yield journey, native, panels


async def private_click(journey, message, data, *, actor=42):
    journey.sequence += 1
    query = CallbackQuery(
        id=str(journey.sequence),
        from_user=User(id=actor, is_bot=False, first_name="Admin"),
        chat_instance="test",
        data=data,
        message=message,
    )
    await journey.dp.feed_update(
        journey.bot, Update(update_id=journey.sequence, callback_query=query)
    )


async def open_panel(journey, database):
    await journey.send("/warn 51", actor=42, chat=CHAT)
    public = sent(journey)[-1]
    async with database() as session:
        mid = (await session.scalar(select(GroupModerationDraft))).panel_message_id
    await click(journey, public.reply_markup.inline_keyboard[0][0].callback_data, message_id=mid)


async def test_navigation_edits_one_ephemeral_panel_and_back_never_publishes(
    ephemeral_journey, database
):
    journey, _, panels = ephemeral_journey
    await open_panel(journey, database)
    assert len(panels) == 1
    key, menu = next(iter(panels.items()))
    before = len(journey.transport.calls)
    await private_click(journey, menu, menu.reply_markup.inline_keyboard[1][0].callback_data)
    confirm = panels[key]
    assert len(panels) == 1 and confirm.ephemeral_message_id == menu.ephemeral_message_id
    yes = confirm.reply_markup.inline_keyboard[0][0].callback_data
    await private_click(journey, confirm, confirm.reply_markup.inline_keyboard[-1][0].callback_data)
    assert len(panels) == 1 and len(panels[key].reply_markup.inline_keyboard) == 4
    assert not any(isinstance(c, SendMessage) for c in journey.transport.calls[before:])
    assert (
        sum(isinstance(c, EditEphemeralMessageText) for c in journey.transport.calls[before:]) == 2
    )
    async with database() as session:
        assert (await session.scalar(select(GroupWarningReset))).state == "CANCELLED"
    # A stale confirmation cannot be resurrected after Back.
    await private_click(journey, confirm, yes)
    assert await active(database) == 1
    menu = panels[key]
    await private_click(journey, menu, menu.reply_markup.inline_keyboard[-1][0].callback_data)
    assert key not in panels


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_confirmed_reset_deletes_or_disables_panel_without_false_failure(
    ephemeral_journey, database, cleanup_fails
):
    journey, _, panels = ephemeral_journey
    await open_panel(journey, database)
    key, menu = next(iter(panels.items()))
    await private_click(journey, menu, menu.reply_markup.inline_keyboard[1][0].callback_data)
    confirm = panels[key]
    journey.transport.fail_private_delete = cleanup_fails
    before = len(journey.transport.calls)
    await private_click(journey, confirm, confirm.reply_markup.inline_keyboard[0][0].callback_data)
    assert await active(database) == 0
    assert (panels[key].reply_markup is None) if cleanup_fails else (key not in panels)
    assert not any(
        isinstance(c, SendMessage) and c.ephemeral_message_parameters
        for c in journey.transport.calls[before:]
    )
    assert not any(
        isinstance(c, (EditMessageText, EditMessageReplyMarkup)) and c.message_id == 0
        for c in journey.transport.calls
    )


async def test_private_rejection_during_back_never_falls_back_to_public(
    ephemeral_journey, database
):
    journey, _, panels = ephemeral_journey
    await open_panel(journey, database)
    key, menu = next(iter(panels.items()))
    await private_click(journey, menu, menu.reply_markup.inline_keyboard[1][0].callback_data)
    confirm = panels[key]
    original = journey.transport.make_request

    async def reject(bot, method, timeout=None):
        if isinstance(method, (EditEphemeralMessageText, SendMessage)):
            journey.transport.calls.append(method)
            raise TelegramForbiddenError(method=method, message="private delivery rejected")
        return await original(bot, method, timeout)

    journey.transport.make_request = reject
    before = len(journey.transport.calls)
    await private_click(journey, confirm, confirm.reply_markup.inline_keyboard[-1][0].callback_data)
    assert not any(
        isinstance(c, SendMessage) and c.chat_id == CHAT and not c.ephemeral_message_parameters
        for c in journey.transport.calls[before:]
    )


async def test_cached_release_job_cannot_report_queued_after_confirmed_unmute(database):
    native = NativeBot()
    await enabled(database, native)
    mute = await confirmed(database, native, await prepared(database, native, "MUTE"))
    worker = LocalModerationExecutor(database, native)
    await worker.process(sanction_id=mute.id)
    release = await prepared(database, native, "UNMUTE", key="cached-release")
    await confirmed(database, native, release)
    async with database() as stale:
        draft = await stale.get(GroupModerationDraft, release.id)
        job = await stale.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == mute.id, GroupSanctionJob.operation == "RELEASE"
            )
        )
        assert job.status == "PENDING"
        await worker.process(sanction_id=mute.id)
        assert job.status == "PENDING"  # Deliberately held, stale ORM object.
        assert await diagnostic(stale, native, draft) is None
        text, done = await receipt_text(stale, draft)
        assert done and "𝗨𝗡𝗠𝗨𝗧𝗘𝗗" in text
        assert t("lm.diagnostic.queued.title") not in text


async def test_queued_ephemeral_receipt_is_removed_when_unmute_finishes(
    ephemeral_journey, database
):
    journey, native, panels = ephemeral_journey
    await journey.send("/mute 51", actor=42, chat=CHAT)
    async with database() as session:
        local = GroupModerationService(session, journey.bot)
        release = await local.prepare(
            CHAT, 42, 51, "UNMUTE", until_date=None, reason="", request="pending-private-unmute"
        )
        release.original_command = "/unmute"
        row = await local.confirm(release.id, 42)
        await public_receipt(session, journey.bot, release)
        assert len(panels) == 1 and release.panel_message_id < 0
        worker = LocalModerationExecutor(database, journey.bot)
        await worker.process(sanction_id=row.id)
        await session.refresh(release)
        assert release.receipt_visibility == "PUBLIC" and release.receipt_done
        assert not panels and native.members[CHAT, 51].status == "member"


async def test_unknown_private_edit_does_not_send_another_window(ephemeral_journey, database):
    journey, _, panels = ephemeral_journey
    await open_panel(journey, database)
    menu = next(iter(panels.values()))
    original = journey.transport.make_request

    async def timeout(bot, method, timeout=None):
        if isinstance(method, EditEphemeralMessageText):
            journey.transport.calls.append(method)
            raise TimeoutError()
        return await original(bot, method, timeout)

    journey.transport.make_request = timeout
    before = len(journey.transport.calls)
    await private_click(journey, menu, menu.reply_markup.inline_keyboard[1][0].callback_data)
    assert len(panels) == 1 and await active(database) == 1
    assert not any(isinstance(c, SendMessage) for c in journey.transport.calls[before:])


async def test_another_admin_cannot_edit_or_delete_recipient_panel(ephemeral_journey, database):
    journey, native, panels = ephemeral_journey
    native.admin(CHAT, 43)
    await open_panel(journey, database)
    key, menu = next(iter(panels.items()))
    before = len(journey.transport.calls)
    await private_click(
        journey, menu, menu.reply_markup.inline_keyboard[-1][0].callback_data, actor=43
    )
    assert key in panels
    assert not any(
        isinstance(
            c, (DeleteEphemeralMessage, EditEphemeralMessageText, EditEphemeralMessageReplyMarkup)
        )
        for c in journey.transport.calls[before:]
    )

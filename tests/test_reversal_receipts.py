"""Original receipts are rewritten; commands also announce the confirmed reversal."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, RestrictChatMember, SendMessage
from sqlalchemy import select
from test_group_moderation_foundation import CHAT
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_warning_controls import click, sent

from app.bot.moderation_messages import BRAND, HEADINGS
from app.i18n import t, use_language
from app.models import GroupModerationDraft
from app.services import Service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize("method", ["command", "button"])
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_release_rewrites_original_and_command_sends_new_confirmation(
    username_journey, database, settings, kind, method, lang
):
    journey, native = username_journey
    native.admin(CHAT, 43)
    async with database() as session:
        service = Service(settings, session)
        await service.observe(43, "releasing_admin", "Release admin")
        await service.set_language(42, lang)
        await service.set_language(43, lang)
    await journey.send("/" + kind.lower() + " 51 1d original-reason", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    async with database() as session:
        draft = await session.scalar(
            select(GroupModerationDraft).where(GroupModerationDraft.kind == kind)
        )
        mid = draft.panel_message_id
    before = len(journey.transport.calls)
    if method == "command":
        await journey.send(
            "/" + ("unban" if kind == "BAN" else "unmute") + " 51", actor=43, chat=CHAT
        )
    else:
        await click(
            journey,
            receipt.reply_markup.inline_keyboard[0][0].callback_data,
            actor=43,
            message_id=mid,
        )
    calls = journey.transport.calls[before:]
    public = [
        c
        for c in calls
        if isinstance(c, SendMessage) and c.chat_id == CHAT and not c.ephemeral_message_parameters
    ]
    assert len(public) == (1 if method == "command" else 0)
    if public:
        assert public[0].text.startswith(HEADINGS["UNBAN" if kind == "BAN" else "UNMUTE"])
    edits = [
        c
        for c in calls
        if isinstance(c, EditMessageText) and c.chat_id == CHAT and c.message_id == mid
    ]
    assert edits and edits[-1].reply_markup is None
    text = edits[-1].text
    assert text.startswith(HEADINGS["UNBAN" if kind == "BAN" else "UNMUTE"])
    assert text.endswith(BRAND) and "original-reason" not in text
    with use_language(lang):
        assert t("lm.notice.duration") not in text
        assert t("lm.control.released") not in text
    # Actor labels may come from the fresh Telegram user, but the stored numeric
    # actor must always be the releaser, not the sanction issuer.
    async with database() as session:
        from app.bot.moderation_messages import success_message
        from app.models import GroupSanction

        origin = await session.scalar(
            select(GroupModerationDraft).where(GroupModerationDraft.kind == kind)
        )
        await Service(settings, session).observe(42, "initial_admin", "Initial")
        await Service(settings, session).observe(43, "releasing_admin", "Release admin")
        row = await session.get(GroupSanction, origin.sanction_id)
        rendered = await success_message(session, origin, row)
        assert "@releasing_admin" in rendered and "42" not in rendered


async def test_warning_clear_rewrites_original_and_keeps_history_button(username_journey, database):
    journey, _ = username_journey
    await journey.send("/warn 51 old-warning-reason", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    async with database() as session:
        mid = (await session.scalar(select(GroupModerationDraft))).panel_message_id
    await click(journey, receipt.reply_markup.inline_keyboard[0][0].callback_data, message_id=mid)
    menu = sent(journey)[-1]
    await click(journey, menu.reply_markup.inline_keyboard[1][0].callback_data, message_id=0)
    confirm = sent(journey)[-1]
    await click(journey, confirm.reply_markup.inline_keyboard[0][0].callback_data, message_id=0)
    edits = [
        c
        for c in journey.transport.calls
        if isinstance(c, EditMessageText) and c.chat_id == CHAT and c.message_id == mid
    ]
    assert edits and edits[-1].text.startswith(HEADINGS["UNWARN"])
    assert edits[-1].text.endswith(BRAND) and "old-warning-reason" not in edits[-1].text
    assert len(edits[-1].reply_markup.inline_keyboard[0]) == 1
    assert "history" in edits[-1].reply_markup.inline_keyboard[0][0].callback_data
    announcements = [
        m
        for m in sent(journey)
        if m.chat_id == CHAT
        and not m.ephemeral_message_parameters
        and m.text.startswith(HEADINGS["UNWARN"])
    ]
    assert len(announcements) == 1
    await click(journey, confirm.reply_markup.inline_keyboard[0][0].callback_data, message_id=0)
    assert (
        len(
            [
                m
                for m in sent(journey)
                if m.chat_id == CHAT
                and not m.ephemeral_message_parameters
                and m.text.startswith(HEADINGS["UNWARN"])
            ]
        )
        == 1
    )


@pytest.mark.parametrize("mode", ["rejected", "timeout"])
async def test_failed_release_keeps_original_and_never_announces_success(
    username_journey, database, mode
):
    journey, native = username_journey
    await journey.send("/mute 51", actor=42, chat=CHAT)
    receipt = sent(journey)[-1]
    async with database() as session:
        mid = (await session.scalar(select(GroupModerationDraft))).panel_message_id
    before = len(journey.transport.calls)
    native.error = (
        TimeoutError()
        if mode == "timeout"
        else TelegramBadRequest(
            method=RestrictChatMember(chat_id=CHAT, user_id=51, permissions={}),
            message="not enough rights",
        )
    )
    await click(journey, receipt.reply_markup.inline_keyboard[0][0].callback_data, message_id=mid)
    calls = journey.transport.calls[before:]
    assert not any(
        isinstance(c, (SendMessage, EditMessageText))
        and c.chat_id == CHAT
        and HEADINGS["UNMUTE"] in c.text
        for c in calls
    )
    assert not journey.transport.deletions
    assert any(
        isinstance(c, SendMessage)
        and c.ephemeral_message_parameters
        and c.ephemeral_message_parameters.receiver_user_id == 42
        for c in calls
    )

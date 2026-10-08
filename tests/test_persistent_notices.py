"""Notifications stay in chat while menus and confirmed identity flows still work."""

from datetime import UTC, datetime

import pytest
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from test_telegram import journey as telegram_journey

from app.bot.callbacks import Action, Moderation, ScamAdmin, TrustedAdmin
from app.bot.screens import preserved_source
from app.bot.states import ScamAdminFlow
from app.i18n import t, use_language
from app.services import Service

journey = telegram_journey


def last_sent(j):
    for index in range(len(j.transport.calls) - 1, -1, -1):
        item = j.transport.calls[index]
        if isinstance(item, (SendMessage, SendPhoto)):
            return index + 1, item
    raise AssertionError("No message sent")


async def click_message(j, data, message_id, *, actor=900):
    j.sequence += 1
    query = CallbackQuery(
        id=str(j.sequence),
        from_user=User(id=actor, is_bot=False, first_name="Tester"),
        chat_instance="test",
        data=data,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=actor, type="private"),
        ),
    )
    await j.dp.feed_update(j.bot, Update(update_id=j.sequence, callback_query=query))


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("chat", [None, -100])
@pytest.mark.parametrize("kind", ["denied", "scam", "trusted"])
async def test_command_notices_survive_following_screens(
    journey, database, settings, lang, chat, kind
):
    actor = 1 if kind == "denied" else 900
    async with database() as session:
        await Service(settings, session).set_language(actor, lang)
    await journey.send("/ask 41", actor=actor, chat=chat)
    previous, _ = last_sent(journey)
    await journey.send(
        "/add_trusted 42" if kind == "trusted" else "/add_sc @receiptuser",
        actor=actor,
        chat=chat,
    )
    receipt, item = last_sent(journey)
    with use_language(lang):
        if kind == "denied":
            assert item.text == t("p.denied")
        elif kind == "scam":
            assert "@receiptuser" in item.text
        else:
            assert "𝗧𝗥𝗨𝗦𝗧𝗘𝗗 • 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗" in item.text
    assert (previous in [call.message_id for call in journey.transport.deletions]) == (
        kind != "denied"
    )
    for command in ("/profile", "/top", "/ask 43"):
        await journey.send(command, actor=actor, chat=chat)
    assert receipt not in [call.message_id for call in journey.transport.deletions]


async def test_service_permission_error_remains_without_discarding_current_panel(journey):
    await journey.send("/profile")
    previous, _ = last_sent(journey)
    await journey.send("/add_trusted 42")
    receipt, item = last_sent(journey)
    assert item.reply_markup is None
    assert (await journey.data())["screen_message_id"] == previous
    await journey.send("/profile")
    await journey.send("/top")
    assert receipt not in [call.message_id for call in journey.transport.deletions]


async def test_unknown_scam_receipt_preserves_id_wizard_and_history(journey, database, settings):
    await journey.send("/add_sc @receiptuser", actor=900)
    receipt, item = last_sent(journey)
    assert await journey.state(900) == ScamAdminFlow.input.state
    assert (await journey.data(900))["screen_message_id"] is None
    names = [
        ScamAdmin.unpack(b.callback_data).action
        for row in item.reply_markup.inline_keyboard
        for b in row
    ]
    assert names == ["id_receipt", "retry_receipt", "page_receipt"]
    await journey.send("42", actor=900)
    preview, _ = last_sent(journey)
    nonce = (await journey.data(900))["scam_nonce"]
    await click_message(journey, ScamAdmin(action="confirm", value=nonce).pack(), preview)
    supplement, item = last_sent(journey)
    assert (await journey.data(900))["screen_message_id"] is None
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["scam"] is not None
    await journey.send("/profile", actor=900)
    await journey.send("/top", actor=900)
    removed = [call.message_id for call in journey.transport.deletions]
    assert receipt not in removed and supplement not in removed
    assert preview in removed


async def test_receipt_buttons_preserve_source_but_replace_current_menu(journey):
    await journey.send("/add_sc @receiptuser", actor=900)
    receipt, item = last_sent(journey)
    back = item.reply_markup.inline_keyboard[-1][0].callback_data
    await journey.send("/profile", actor=900)
    previous, _ = last_sent(journey)
    await click_message(journey, back, receipt)
    panel, _ = last_sent(journey)
    removed = [call.message_id for call in journey.transport.deletions]
    assert receipt not in removed and previous in removed
    assert (await journey.data(900))["screen_message_id"] == panel
    assert preserved_source.get() is None
    await click_message(journey, Action(name="admin").pack(), panel)
    assert panel in [call.message_id for call in journey.transport.deletions]
    assert receipt not in [call.message_id for call in journey.transport.deletions]


async def test_scam_removal_receipt_admin_back_preserves_history(journey):
    await journey.send("/add_sc 42", actor=900)
    added, _ = last_sent(journey)
    await journey.send("/del_sc 42 Reviewed appeal evidence", actor=900)
    removed, item = last_sent(journey)
    back = item.reply_markup.inline_keyboard[0][0].callback_data
    assert Action.unpack(back).value == "receipt"
    await journey.send("/profile", actor=900)
    previous, _ = last_sent(journey)
    await click_message(journey, back, removed)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert previous in deleted and added not in deleted and removed not in deleted
    assert preserved_source.get() is None


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_report_decision_receipt_and_back_preserve_chat_history(
    journey, database, settings, lang, decision
):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        report = await core.submit_report(
            1, "42", "Reviewed payment evidence", [], "receipt-report"
        )
        reference = report.reference
    await journey.click(Action(name="pending").pack(), actor=900)
    panel, _ = last_sent(journey)
    await click_message(journey, Moderation(action=decision, reference=reference).pack(), panel)
    receipt, item = last_sent(journey)
    assert (await journey.data(900))["screen_message_id"] is None
    if decision == "approve":
        assert "𝗦𝗖𝗔𝗠 • 𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗" in item.text
    back = item.reply_markup.inline_keyboard[0][0].callback_data
    assert Action.unpack(back).name == "pending"
    await journey.send("/profile", actor=900)
    previous, _ = last_sent(journey)
    await click_message(journey, back, receipt)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert panel in deleted and previous in deleted and receipt not in deleted
    assert preserved_source.get() is None


async def test_persistent_scam_controls_still_require_admin(journey):
    await journey.send("/add_sc @receiptuser", actor=900)
    receipt, item = last_sent(journey)
    button = item.reply_markup.inline_keyboard[0][0].callback_data
    await click_message(journey, button, receipt, actor=1)
    assert await journey.state(1) is None
    assert receipt not in [call.message_id for call in journey.transport.deletions]
    assert preserved_source.get() is None


async def test_receipt_id_button_keeps_receipt_and_cancel_removes_only_prompt(journey):
    await journey.send("/add_sc @receiptuser", actor=900)
    receipt, item = last_sent(journey)
    button = item.reply_markup.inline_keyboard[0][0].callback_data
    await journey.send("/profile", actor=900)
    previous, _ = last_sent(journey)
    await click_message(journey, button, receipt)
    prompt, _ = last_sent(journey)
    assert await journey.state(900) == ScamAdminFlow.input.state
    deleted = [call.message_id for call in journey.transport.deletions]
    assert previous in deleted and receipt not in deleted
    await journey.send("/cancel", actor=900)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert prompt in deleted and receipt not in deleted
    assert await journey.state(900) is None


@pytest.mark.parametrize("chat", [None, -100])
async def test_trusted_grant_and_removal_receipts_both_remain(journey, chat):
    await journey.send("/add_trusted 42", actor=900, chat=chat)
    grant, _ = last_sent(journey)
    await journey.send("/del_trusted 42", actor=900, chat=chat)
    removal, _ = last_sent(journey)
    await journey.send("/profile", actor=900, chat=chat)
    await journey.send("/top", actor=900, chat=chat)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert grant not in deleted and removal not in deleted


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_admin_trusted_removal_receipt_back_preserves_notice(
    journey, database, settings, lang
):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        await core.set_trusted(900, "42", True, "test-grant")
        target = (await core.resolve("42")).id
    await journey.click(TrustedAdmin(action="remove", value=str(target)).pack(), actor=900)
    preview, _ = last_sent(journey)
    nonce = (await journey.data(900))["trusted_nonce"]
    await click_message(journey, TrustedAdmin(action="confirm", value=nonce).pack(), preview)
    receipt, item = last_sent(journey)
    with use_language(lang):
        assert t("tm.done") in item.text
    back = item.reply_markup.inline_keyboard[0][0].callback_data
    assert TrustedAdmin.unpack(back).action == "page_receipt"
    # Stay in the same administrative section so the expected Back context remains.
    await click_message(journey, TrustedAdmin(action="page", value="0").pack(), receipt)
    previous, _ = last_sent(journey)
    await click_message(journey, back, receipt)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert previous in deleted and receipt not in deleted and preview in deleted
    async with database() as session:
        assert not (await Service(settings, session).profile("42"))["trusted"]
    assert preserved_source.get() is None

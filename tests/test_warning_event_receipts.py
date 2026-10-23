"""Event-time WARN facts and real router/SQL paths; Telegram is a transport double."""

import asyncio
from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import BanChatMember, EditMessageCaption, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update, User
from sqlalchemy import func, select
from test_checks import due, events, prepared, settings_for
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_telegram import journey as journey
from test_warning_controls import active, sent, warn

from app.bot import keyboards as kb
from app.bot.checks_runtime import process_checks, security_text, words_text
from app.bot.local_admin import receipt_text
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_messages import HEADINGS, duration_text
from app.bot.screens import render
from app.checks import observe_words
from app.i18n import t, use_language
from app.models import (
    GroupCheckEvent,
    GroupModerationDraft,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    GroupWarningEscalation,
    now,
)
from app.moderation_services import GroupModerationService
from app.services import Service
from app.warning_services import reset_prompt, reset_warnings
from app.warning_snapshots import warning_snapshot

pytestmark = pytest.mark.asyncio


def public_messages(journey):
    return [m for m in sent(journey) if m.chat_id == CHAT and not m.ephemeral_message_parameters]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("kind", ["MUTE", "BAN", "KICK"])
async def test_threshold_has_only_final_action_and_immutable_prior_warns(
    username_journey, database, settings, lang, kind
):
    j, bot = username_journey
    bot.members[CHAT, 42].user.username = "actual_admin"
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
        policy = await session.get(GroupModerationSettings, CHAT)
        policy.warning_action, policy.warning_mute_seconds = kind, 3600
        await session.commit()
    await j.send("/warn 51 first", actor=42, chat=CHAT)
    first = public_messages(j)[-1]
    await j.send("/warn 51 second", actor=42, chat=CHAT)
    second = public_messages(j)[-1]
    await j.send("/warn 51 third", actor=42, chat=CHAT)
    public = public_messages(j)
    assert len(public) == 3
    assert "1/3" in first.text and "2/3" in second.text
    assert HEADINGS[kind] in public[-1].text and "3 WARN" in public[-1].text
    assert not any("3/3" in m.text for m in public)
    assert "@actual_admin" in public[-1].text
    with use_language(lang):
        assert t("lm.control.auto_reason", limit=3) in public[-1].text
        if kind == "MUTE":
            assert duration_text(3600, kind) in public[-1].text
    async with database() as session:
        drafts = list(
            await session.scalars(
                select(GroupModerationDraft)
                .where(GroupModerationDraft.kind == "WARN")
                .order_by(GroupModerationDraft.created_at)
            )
        )
        original_ids = {d.panel_message_id for d in drafts[:2]}
        assert not any(
            isinstance(m, EditMessageText) and m.chat_id == CHAT and m.message_id in original_ids
            for m in j.transport.calls
        )
        assert drafts[2].panel_message_id is None
        assert await session.scalar(select(func.count()).select_from(GroupWarningEscalation)) == 1
        # A later policy edit must not rewrite the issued denominator either.
        (await session.get(GroupModerationSettings, CHAT)).warning_limit = 5
        await session.commit()
        for i, draft in enumerate(drafts[:2], 1):
            with use_language(lang):
                text, done = await receipt_text(session, draft)
            assert done and f"{i}/3" in text
    assert await active(database) == 0
    assert len(bot.calls) == (2 if kind == "KICK" else 1)


@pytest.mark.parametrize("failure", ["reject", "timeout"])
async def test_failed_threshold_retains_three_warns_without_success_notice(
    username_journey, database, failure
):
    j, bot = username_journey
    method = BanChatMember(chat_id=CHAT, user_id=51)
    bot.error = (
        TelegramBadRequest(method=method, message="not enough rights")
        if failure == "reject"
        else TimeoutError()
    )
    for _ in range(3):
        await j.send("/warn 51", actor=42, chat=CHAT)
    assert await active(database) == 3
    messages = public_messages(j)
    assert len(messages) == 3 and "3/3" in messages[-1].text
    assert all(HEADINGS["MUTE"] not in m.text for m in messages)


async def test_floodwait_defers_trigger_warn_then_announces_one_success(username_journey, database):
    j, bot = username_journey
    for _ in range(2):
        await j.send("/warn 51", actor=42, chat=CHAT)
    bot.error = TelegramRetryAfter(
        method=BanChatMember(chat_id=CHAT, user_id=51), message="wait", retry_after=30
    )
    await j.send("/warn 51", actor=42, chat=CHAT)
    assert len(public_messages(j)) == 2 and await active(database) == 3
    bot.error = None
    async with database() as session:
        job = await session.scalar(
            select(GroupSanctionJob).where(GroupSanctionJob.operation == "APPLY")
        )
        job.available_at = now()
        sanction_id = job.sanction_id
        await session.commit()
    await LocalModerationExecutor(database, j.bot).process(sanction_id=sanction_id)
    messages = public_messages(j)
    assert len(messages) == 3 and HEADINGS["MUTE"] in messages[-1].text
    assert await active(database) == 0


async def test_cancelled_warns_keep_their_issued_counts(database):
    bot = NativeBot()
    await enabled(database, bot)
    first, _ = await warn(database, bot, "snapshot-first")
    second, sid = await warn(database, bot, "snapshot-second")
    async with database() as session:
        local = GroupModerationService(session, bot)
        origin = await session.get(GroupSanction, sid)
        prompt = await reset_prompt(local, origin, 42)
        await reset_warnings(local, prompt.id, 42)
        for i, identifier in enumerate((first, second), 1):
            draft = await session.get(GroupModerationDraft, identifier)
            text, _ = await receipt_text(session, draft)
            assert f"{i}/3" in text and "UNWARNED" not in text
    assert await active(database) == 0


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_security_link_uses_display_name_and_real_numeric_id(lang):
    event = GroupCheckEvent(
        telegram_id=8711833130, display_name='GuCC1 <&> "', action="KICK", rule="username"
    )
    user = User(
        id=event.telegram_id,
        first_name='GuCC1 <&> "',
        username="not_the_display_name",
        is_bot=False,
    )
    with use_language(lang):
        for text in (security_text(event, user), words_text(event, warnings=2, warning_limit=5)):
            assert 'href="tg://user?id=8711833130"' in text
            assert "GuCC1 &lt;&amp;&gt; &quot;" in text and "not_the_display_name" not in text
            assert "[<code>8711833130</code>]" in text
        assert "2/5" in words_text(event, warnings=2, warning_limit=5)


@pytest.mark.parametrize("action", ["OFF", "WARN"])
async def test_word_counter_is_real_scoped_and_does_not_double_warn(database, action):
    bot = await prepared(database)
    async with database() as session:
        (await session.get(GroupModerationSettings, CHAT)).warning_limit = 5
        await session.commit()
    await warn(database, bot, "words-existing-one")
    await settings_for(database, bot, actions={}, banned_words=["filtered"], words_action=action)
    assert await observe_words(
        database,
        bot,
        CHAT,
        bot.person(name="Actual name", username="other_username"),
        700,
        "filtered",
    )
    await process_checks(database, bot)
    assert "1/5" in bot.notices[-1][1]
    if action == "WARN":
        await due(database)
        await process_checks(database, bot)
        event = (await events(database))[0]
        await LocalModerationExecutor(database, bot).process(sanction_id=event.sanction_id)
        await process_checks(database, bot)
        assert "2/5" in bot.edits[-1][0]
    assert await active(database) == (2 if action == "WARN" else 1)
    await process_checks(database, bot)
    assert await active(database) == (2 if action == "WARN" else 1)


async def test_word_threshold_announces_automatic_mute_without_public_warn_origin(database):
    bot = await prepared(database)
    for i in range(2):
        await warn(database, bot, f"filter-threshold-{i}")
    await settings_for(database, bot, actions={}, banned_words=["filtered"], words_action="WARN")
    await observe_words(database, bot, CHAT, bot.person(), 702, "filtered")
    await process_checks(database, bot)
    await due(database)
    await process_checks(database, bot)
    event = (await events(database))[0]
    await LocalModerationExecutor(database, bot).process(sanction_id=event.sanction_id)
    await process_checks(database, bot)
    assert await active(database) == 0
    assert "3/3" in bot.edits[-1][0]
    assert sum(HEADINGS["MUTE"] in text for _, text, _ in bot.notices) == 1
    assert sum("WARNED" in text for _, text, _ in bot.notices) == 0


async def test_postgres_concurrent_warns_snapshot_and_escalate_once(postgres_contract):
    factory, _ = postgres_contract
    bot = NativeBot()
    await enabled(factory, bot)
    await warn(factory, bot, "parallel-snapshot-one")
    results = await asyncio.gather(
        warn(factory, bot, "parallel-snapshot-two"), warn(factory, bot, "parallel-snapshot-three")
    )
    async with factory() as session:
        counts = [
            await warning_snapshot(session, await session.get(GroupSanction, sid))
            for _, sid in results
        ]
        assert sorted(c[0] for c in counts) == [2, 3]
        assert await session.scalar(select(func.count()).select_from(GroupWarningEscalation)) == 1
    await asyncio.gather(
        LocalModerationExecutor(factory, bot).process(),
        LocalModerationExecutor(factory, bot).process(),
    )
    assert len(bot.calls) == 1 and await active(factory) == 0
    assert sum(HEADINGS["MUTE"] in text for _, text, _ in bot.messages) == 1


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("chat", [1, CHAT])
async def test_check_profile_roundtrip_edits_one_message(journey, database, settings, lang, chat):
    j = journey
    async with database() as session:
        service = Service(settings, session)
        await service.observe(51, "known_target", "Display name")
        await service.set_language(1, lang)
    markup = kb.check_result(51, persistent=True)
    for index in range(4):
        data = (
            kb.action("profile", "51")
            if index % 2 == 0
            else markup.inline_keyboard[-1][0].callback_data
        )
        message = Message(
            message_id=700,
            date=datetime.now(UTC),
            chat=Chat(id=chat, type="private" if chat == 1 else "supergroup"),
            text="Check/profile",
            reply_markup=markup,
        )
        await j.dp.feed_update(
            j.bot,
            Update(
                update_id=9100 + index,
                callback_query=CallbackQuery(
                    id=str(9100 + index),
                    from_user=User(id=1, first_name="Actor", is_bot=False),
                    chat_instance="nav",
                    data=data,
                    message=message,
                ),
            ),
        )
        edits = [m for m in j.transport.calls if isinstance(m, EditMessageText)]
        assert edits and edits[-1].message_id == 700
        markup = edits[-1].reply_markup
        if index % 2:
            assert markup.inline_keyboard[0][0].callback_data == kb.action("profile", "51")
    assert not any(isinstance(m, SendMessage) for m in j.transport.calls)
    assert not j.transport.deletions


async def test_profile_media_caption_and_uneditable_fallback(journey):
    j = journey
    photo = Message(
        message_id=701,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        photo=[PhotoSize(file_id="photo", file_unique_id="unique", width=100, height=100)],
        caption="Check",
    ).as_(j.bot)
    await render(photo, "<b>Profile</b>", reply_markup=kb.check_result(51), interactive=True)
    assert isinstance(j.transport.calls[-1], EditMessageCaption)
    assert j.transport.calls[-1].message_id == 701 and not j.transport.deletions
    original = j.transport.make_request

    async def fail_edit(bot, method, timeout=None):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message="message can't be edited")
        return await original(bot, method, timeout)

    j.transport.make_request = fail_edit
    text = Message(
        message_id=702, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text="Check"
    ).as_(j.bot)
    await render(text, "Profile", reply_markup=kb.check_result(51), interactive=True)
    assert sum(isinstance(m, SendMessage) for m in j.transport.calls) == 1
    assert j.transport.deletions[-1].message_id == 702


@pytest.mark.parametrize("limit", [2, 5])
async def test_nondefault_limit_is_frozen_and_only_one_automatic_action(
    username_journey, database, limit
):
    j, bot = username_journey
    async with database() as session:
        (await session.get(GroupModerationSettings, CHAT)).warning_limit = limit
        await session.commit()
    for _ in range(limit):
        await j.send("/warn 51", actor=42, chat=CHAT)
    messages = public_messages(j)
    assert len(messages) == limit
    for i, message in enumerate(messages[:-1], 1):
        assert f"{i}/{limit}" in message.text
    assert f"{limit} WARN" in messages[-1].text and HEADINGS["MUTE"] in messages[-1].text
    assert await active(database) == 0 and len(bot.calls) == 1


@pytest.mark.parametrize("failure", ["flood", "missing"])
async def test_word_counter_edit_failure_cannot_reapply_warning(database, failure):
    bot = await prepared(database)
    await settings_for(database, bot, actions={}, banned_words=["filtered"], words_action="WARN")
    await observe_words(database, bot, CHAT, bot.person(), 703, "filtered")
    await process_checks(database, bot)
    await due(database)
    await process_checks(database, bot)
    event = (await events(database))[0]
    await LocalModerationExecutor(database, bot).process(sanction_id=event.sanction_id)
    original = bot.edit_message_text

    async def fail(*args, **kwargs):
        method = EditMessageText(chat_id=CHAT, message_id=1, text="counter")
        if failure == "flood":
            raise TelegramRetryAfter(method=method, message="wait", retry_after=60)
        raise TelegramBadRequest(method=method, message="message to edit not found")

    bot.edit_message_text = fail
    await process_checks(database, bot)
    current = (await events(database))[0]
    assert await active(database) == 1
    assert current.state == ("QUEUED" if failure == "flood" else "DONE")
    bot.edit_message_text = original
    if failure == "flood":
        async with database() as session:
            (await session.get(GroupCheckEvent, current.id)).available_at = now()
            await session.commit()
        await process_checks(database, bot)
        assert "1/3" in bot.edits[-1][0]
    assert await active(database) == 1 and len(bot.notices) == 1


async def test_repeated_caption_and_text_edits_do_not_create_panels(journey):
    j = journey
    original = j.transport.make_request

    async def unchanged(bot, method, timeout=None):
        if isinstance(method, (EditMessageText, EditMessageCaption)):
            raise TelegramBadRequest(method=method, message="message is not modified")
        return await original(bot, method, timeout)

    j.transport.make_request = unchanged
    message = Message(
        message_id=703, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text="Profile"
    ).as_(j.bot)
    await render(message, "Profile", interactive=True)
    assert not any(isinstance(m, SendMessage) for m in j.transport.calls)
    assert not j.transport.deletions
    j.transport.make_request = original
    photo = Message(
        message_id=704,
        date=datetime.now(UTC),
        chat=Chat(id=1, type="private"),
        photo=[PhotoSize(file_id="p", file_unique_id="u", width=10, height=10)],
    ).as_(j.bot)
    await render(photo, "💎" * 600, interactive=True)
    assert sum(isinstance(m, SendMessage) for m in j.transport.calls) == 1
    assert j.transport.deletions[-1].message_id == 704


async def test_forged_profile_callback_cannot_overwrite_moderation_history(
    username_journey, database
):
    j, _ = username_journey
    await j.send("/warn 51", actor=42, chat=CHAT)
    async with database() as session:
        draft = await session.scalar(
            select(GroupModerationDraft).where(GroupModerationDraft.kind == "WARN")
        )
        message_id = draft.panel_message_id
    before = len(j.transport.calls)
    await j.click(kb.action("profile", "51"), actor=42, chat=CHAT, message_id=message_id)
    assert not any(
        isinstance(m, (EditMessageText, SendMessage)) for m in j.transport.calls[before:]
    )
    assert j.transport.calls[-1].show_alert

"""Actual SQL and router tests. Telegram calls are stateful doubles, not live proof."""

from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage, EditMessageText, SendMessage
from sqlalchemy import select
from test_checks import CheckBot, due, events, prepared, settings_for
from test_group_moderation_foundation import CHAT, OTHER, setup_groups
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import local_journey as local_journey

from app.bot.callbacks import CheckSettings, WarningSettings
from app.bot.checks_runtime import process_checks
from app.bot.group_help import cb
from app.bot.keyboards import action
from app.bot.local_moderation import LocalModerationExecutor
from app.checks import configure, observe, observe_words, word_match
from app.errors import DomainError
from app.i18n import use_language
from app.models import GroupCheckEvent, GroupCheckMember, GroupCheckSettings, GroupSanction, now
from app.services import Service

pytestmark = pytest.mark.asyncio


async def make_due(factory):
    async with factory() as session:
        for e in await session.scalars(
            select(GroupCheckEvent).where(GroupCheckEvent.state == "WAITING")
        ):
            e.notice_sent_at = now() - timedelta(seconds=e.grace_seconds + 1)
            e.execute_after = e.notice_sent_at + timedelta(seconds=e.grace_seconds)
            e.available_at = e.execute_after
        await session.commit()


async def rejoin_series(factory):
    bot = await prepared(factory)
    # Multiple KICK violations count as a single entry.
    await settings_for(factory, bot, actions={"username": "KICK", "surname": "KICK"})
    first_join = now() - timedelta(minutes=10)
    for number in range(1, 5):
        user = bot.person()
        joined = first_join + timedelta(minutes=number)
        await observe(factory, bot, CHAT, user, joined_at=joined, check_join=True)
        # Duplicate delivery / messages cannot create an extra attempt.
        await observe(factory, bot, CHAT, user, joined_at=joined, check_join=True)
        await observe(factory, bot, CHAT, user, message_id=number)
        await process_checks(factory, bot)
        e = next(e for e in reversed(await events(factory)) if e.state == "WAITING")
        assert e.kick_attempt == number
        assert e.grace_seconds == (15 if number == 1 else 5 if number < 4 else 0)
        assert e.action == ("KICK" if number < 4 else "BAN")
        assert "sekund" not in bot.notices[-1][1] and "⏳" not in bot.notices[-1][1]
        async with factory() as session:
            member = await session.get(GroupCheckMember, (CHAT, 51))
            assert member.kick_attempts == number
        if number < 4:
            await process_checks(factory, bot)
            assert not any(e.sanction_id for e in await events(factory) if e.kick_attempt == number)
        await make_due(factory)
        await process_checks(factory, bot)
        e = next(e for e in reversed(await events(factory)) if e.state == "QUEUED")
        await LocalModerationExecutor(factory, bot).process(limit=2, sanction_id=e.sanction_id)
        await process_checks(factory, bot)
        async with factory() as session:
            sanction = await session.get(GroupSanction, e.sanction_id)
            if number < 4:
                assert sanction.status == "COMPLETED"
            else:
                assert sanction.kind == "BAN" and sanction.status == "ACTIVE"
                assert (
                    86380
                    < (sanction.until_date.replace(tzinfo=now().tzinfo) - now()).total_seconds()
                    <= 86400
                )
                assert "24" in bot.edits[-1][0] and "Norėdami prisijungti" not in bot.edits[-1][0]
    assert [c[0] for c in bot.calls] == ["ban", "unban"] * 3 + ["ban"]


async def test_four_rejoins_are_15_5_5_then_24_hour_ban(database):
    await rejoin_series(database)


async def test_postgres_four_rejoins(postgres_contract):
    await rejoin_series(postgres_contract[0])


async def test_compliance_resets_attempts_and_cancels_pending(database):
    bot = await prepared(database)
    user = bot.person()
    await observe(database, bot, CHAT, user, joined_at=now(), check_join=True)
    await process_checks(database, bot)
    fixed = bot.person(username="fixed_name")
    await observe(database, bot, CHAT, fixed, message_id=15)
    await make_due(database)
    await process_checks(database, bot)
    assert not bot.calls
    async with database() as session:
        assert (await session.get(GroupCheckMember, (CHAT, 51))).kick_attempts == 0
    user = bot.person()
    await observe(
        database, bot, CHAT, user, joined_at=now() + timedelta(minutes=1), check_join=True
    )
    assert (await events(database))[-1].kick_attempt == 1


@pytest.mark.parametrize("action", ["OFF", "WARN", "KICK", "MUTE", "BAN"])
async def test_words_delete_notice_then_optional_native_action(database, action):
    bot = await prepared(database)
    await settings_for(
        database, bot, actions={}, banned_words=["filtered", "two words"], words_action=action
    )
    user = bot.person(name="Real <&> Name")
    assert await observe_words(database, bot, CHAT, user, 701, "A FILTERED message")
    assert not await observe_words(database, bot, OTHER, user, 701, "filtered")
    await process_checks(database, bot)
    e = (await events(database))[0]
    assert bot.deletions == [(CHAT, 701)] and e.message_deleted
    assert len(bot.notices) == 1 and not bot.calls
    text = bot.notices[0][1]
    assert "Real &lt;&amp;&gt; Name" in text
    assert "<b>Pranešimas pašalintas:</b>" in text
    await observe_words(database, bot, CHAT, user, 701, "filtered")
    await process_checks(database, bot)
    assert len(bot.deletions) == len(bot.notices) == 1
    if action == "OFF":
        assert e.state == "DONE"
        with use_language("lt"):
            assert text == (
                "🛡 <b>REDSAFE SECURITY</b>\n\n"
                "👤 <b>Real &lt;&amp;&gt; Name</b> [<code>51</code>]\n\n"
                "⚠️ <b>Pranešimas pašalintas:</b> Aptiktas filtruojamas raktažodis.\n\n"
                "<i>Prašome naudoti savus terminus arba alternatyvius pavadinimus, kad pranešimai nebūtų automatiškai šalinami.</i>"
            )
        return
    await due(database)
    await process_checks(database, bot)
    e = (await events(database))[0]
    assert e.state == "QUEUED"
    await LocalModerationExecutor(database, bot).process(limit=2, sanction_id=e.sanction_id)
    await process_checks(database, bot)
    assert (await events(database))[0].state == "DONE"


async def test_deletion_failure_never_claims_removed_or_punishes(database):
    bot = await prepared(database)
    await settings_for(database, bot, actions={}, banned_words=["blocked"], words_action="BAN")

    async def fail(*args):
        raise TelegramBadRequest(
            method=DeleteMessage(chat_id=CHAT, message_id=3), message="not enough rights"
        )

    bot.delete_message = fail
    await observe_words(database, bot, CHAT, bot.person(), 3, "blocked")
    await process_checks(database, bot)
    assert not bot.notices and not bot.calls
    assert (await events(database))[0].state == "UNCERTAIN"


async def test_deletion_off_and_clean_edit_cancel_unclaimed_punishment(database):
    bot = await prepared(database)
    await settings_for(
        database, bot, actions={}, banned_words=["blocked"], words_action="KICK", words_delete=False
    )
    user = bot.person()
    await observe_words(database, bot, CHAT, user, 3, "blocked")
    await process_checks(database, bot)
    assert not bot.deletions and "Pranešimas pašalintas" not in bot.notices[0][1]
    await make_due(database)
    await process_checks(database, bot)
    await observe_words(database, bot, CHAT, user, 3, "clean now")
    await LocalModerationExecutor(database, bot).process(limit=2)
    assert not bot.calls


@pytest.mark.parametrize(
    ("text", "term", "matches"),
    [
        ("Bad word!", "bad word", True),
        ("BAD", "bad", True),
        ("badminton", "bad", False),
        ("𝓫𝓪𝓭", "bad", True),
        ("[a+b]", "a+b", True),
        ("nebėra", "bėra", False),
        ("слово!", "слово", True),
        ("žodis.", "žodis", True),
    ],
)
async def test_literal_unicode_whole_word_matching(text, term, matches):
    assert bool(word_match(text, [term])) is matches


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_group_first_navigation_words_edit_back_and_no_new_windows(
    local_journey, database, settings, lang
):
    journey, native = local_journey
    native.members[CHAT, native.id].can_delete_messages = True
    async with database() as session:
        s = Service(settings, session)
        await s.set_language(42, lang)
        await s.access.change(900, "42", True, "b" * 32)
    await journey.click(action("group_help"), actor=42)

    def screen():
        return next(
            c
            for c in reversed(journey.transport.calls)
            if isinstance(c, (EditMessageText, SendMessage))
        )

    def routes():
        return [b.callback_data for row in screen().reply_markup.inline_keyboard for b in row]

    assert cb("group", CHAT) in routes()
    await journey.click(cb("group", CHAT), actor=42)
    assert WarningSettings(action="policy", chat=CHAT).pack() in routes()
    assert CheckSettings(action="words", chat=CHAT).pack() in routes()
    assert not any(v.startswith("lm2|") for v in routes())
    sent = len([c for c in journey.transport.calls if isinstance(c, SendMessage)])
    await journey.click(CheckSettings(action="words", chat=CHAT).pack(), actor=42)
    add = next(v for v in routes() if v.startswith("ck|w_add|"))
    await journey.click(add, actor=42)
    await journey.send("firstword\nsecond phrase", actor=42)
    async with database() as session:
        policy = await session.get(GroupCheckSettings, CHAT)
        assert policy.banned_words == ["firstword", "second phrase"]
        assert await session.get(GroupCheckSettings, OTHER) is None
    await journey.click(cb("group", CHAT), actor=42)
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == sent
    native.members[CHAT, 42].can_change_info = False
    await journey.click(CheckSettings(action="words", chat=CHAT).pack(), actor=42)
    assert journey.transport.calls[-1].show_alert is True


async def test_unconfigured_commands_require_authority_but_no_module_menu(database):
    bot = CheckBot()
    await setup_groups(database, bot)
    from app.moderation_services import GroupModerationService

    async with database() as session:
        local = GroupModerationService(session, bot)
        with pytest.raises(DomainError):
            await local.ensure_commands(CHAT, 51)
        await local.ensure_commands(CHAT, 42)
        draft = await local.prepare(
            CHAT, 42, 51, "KICK", request="no-module-switch", until_date=None, reason=""
        )
        assert draft.kind == "KICK"


async def test_postgres_concurrent_word_delivery_is_idempotent(postgres_contract):
    import asyncio

    factory = postgres_contract[0]
    bot = await prepared(factory)
    await settings_for(factory, bot, actions={}, banned_words=["filtered"])
    user = bot.person()
    await asyncio.gather(
        *(observe_words(factory, bot, CHAT, user, 777, "filtered") for _ in range(3))
    )
    await asyncio.gather(*(process_checks(factory, bot) for _ in range(2)))
    assert len(await events(factory)) == 1
    assert bot.deletions == [(CHAT, 777)]
    assert len(bot.notices) == 1


async def test_media_captions_and_edited_text_are_checked_by_real_router(local_journey, database):
    from aiogram.types import Chat, Message, Update, User

    journey, native = local_journey
    native.members[CHAT, native.id].can_delete_messages = True
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user = User(id=51, is_bot=False, first_name="Real")
    await settings_for(database, native, actions={}, banned_words=["filtered"])
    await journey.send(actor=51, chat=CHAT, caption="filtered caption")
    assert (await events(database))[0].rule == "words"
    await journey.send("clean text", actor=51, chat=CHAT)
    message = Message(
        message_id=journey.sequence,
        date=now(),
        chat=Chat(id=CHAT, type="supergroup"),
        from_user=User(id=51, is_bot=False, first_name="Real"),
        text="now filtered",
    )
    await journey.dp.feed_update(journey.bot, Update(update_id=1234567, edited_message=message))
    assert len(await events(database)) == 2


async def test_words_settings_stale_update_cannot_touch_another_group(database):
    bot = await prepared(database)
    p = await settings_for(database, bot, actions={}, banned_words=["one"])
    await settings_for(database, bot, banned_words=["two"])
    async with database() as session:
        with pytest.raises(DomainError, match="checks_stale"):
            await configure(session, bot, CHAT, 42, p.version, banned_words=["lost"])
        assert await session.get(GroupCheckSettings, OTHER) is None
        assert (await session.get(GroupCheckSettings, CHAT)).banned_words == ["two"]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_notice_localizations_preserve_html_and_hide_seconds(lang):
    from app.bot.checks_runtime import security_text, words_text

    e = GroupCheckEvent(
        telegram_id=8896102663,
        display_name="Name <&>",
        rule="username",
        action="KICK",
        kick_attempt=2,
    )
    with use_language(lang):
        text = security_text(e, planned=True)
        assert "Name &lt;&amp;&gt;" in text and "[<code>8896102663</code>]" in text
        assert "<b>REDSAFE SECURITY</b>" in text and "<i>" in text
        assert "sekund" not in text and "seconds" not in text and "секунд" not in text
        e.action, e.rule, e.message_deleted = "DELETE", "words", True
        text = words_text(e)
        assert "<b>REDSAFE SECURITY</b>" in text and "<i>" in text


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_add_help_cancel_and_remove_prompts_match_the_actual_list(
    local_journey, database, settings, lang
):
    from app.bot.checks_ui import cb as ck

    journey, native = local_journey
    native.members[CHAT, native.id].can_delete_messages = True
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await settings_for(database, native, banned_words=["one", "two"])

    def last():
        return next(
            c
            for c in reversed(journey.transport.calls)
            if isinstance(c, (EditMessageText, SendMessage))
        )

    await journey.click(ck("words", CHAT), actor=42)
    button = next(
        b
        for row in last().reply_markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("ck|w_add|")
    )
    await journey.click(button.callback_data, actor=42)
    assert "<b>" in last().text and "<i>" in last().text
    help_button = last().reply_markup.inline_keyboard[0][0].callback_data
    await journey.click(help_button, actor=42)
    assert await journey.state(actor=42) == "CheckSettingsFlow:terms"
    cancel = last().reply_markup.inline_keyboard[-1][0].callback_data
    await journey.click(cancel, actor=42)
    assert await journey.state(actor=42) is None
    remove = next(
        b
        for row in last().reply_markup.inline_keyboard
        for b in row
        if b.callback_data.startswith("ck|w_remove|")
    )
    await journey.click(remove.callback_data, actor=42)
    assert len(last().reply_markup.inline_keyboard) == 1
    with use_language(lang):
        from app.i18n import t

        assert last().text == t("bw.remove_prompt")
    await journey.send("ONE", actor=42)
    async with database() as session:
        assert (await session.get(GroupCheckSettings, CHAT)).banned_words == ["two"]


@pytest.mark.parametrize("command", ["ban", "mute", "warn", "kick", "warns"])
async def test_first_command_in_new_group_needs_no_group_selection_or_global_approval(
    local_journey, database, command
):
    from app.models import GroupModerationSettings, ManagedGroup

    journey, native = local_journey
    chat = -1009988776655
    native.admin(chat, 42)
    native.members[chat, native.id] = native.peer(
        native.id, "administrator", is_bot=True, can_restrict_members=True, can_change_info=True
    )
    await journey.send(
        "/" + command + " 51" + (" 1h" if command in {"ban", "mute"} else ""), actor=42, chat=chat
    )
    async with database() as session:
        group = await session.get(ManagedGroup, chat)
        assert group is not None and group.approved is False and group.enabled is False
        policy = await session.get(GroupModerationSettings, chat)
        assert policy.enabled is True and policy.local_auto_enabled is True
        sanctions = list(
            await session.scalars(select(GroupSanction).where(GroupSanction.chat_id == chat))
        )
        assert len(sanctions) == (0 if command == "warns" else 1)
    assert all(
        c.chat_id == chat or c.chat_id == 42
        for c in journey.transport.calls
        if isinstance(c, SendMessage)
    )
    assert not any(
        isinstance(c, (SendMessage, EditMessageText))
        and c.reply_markup
        and any(
            (b.callback_data or "").startswith("gh|group|")
            for r in c.reply_markup.inline_keyboard
            for b in r
        )
        for c in journey.transport.calls
    )


async def test_unauthorized_new_group_cannot_be_enrolled_and_removal_is_respected(
    local_journey, database
):
    from app.models import GroupModerationSettings, ManagedGroup

    journey, native = local_journey
    chat = -1009988776655
    for actor in (1, 900):
        await journey.send("/ban 51 1h", actor=actor, chat=chat)
    async with database() as session:
        assert await session.get(ManagedGroup, chat) is None
        assert await session.get(GroupModerationSettings, chat) is None
    assert not native.calls


async def test_local_only_group_rights_loss_stops_pending_native_work(database, settings):
    from app.group_services import GroupService
    from app.models import GroupModerationSettings, ManagedGroup
    from app.moderation_services import GroupModerationService

    bot = CheckBot()
    await setup_groups(database, bot)
    async with database() as session:
        group = await session.get(ManagedGroup, CHAT)
        group.approved = group.enabled = False
        await session.commit()
        local = GroupModerationService(session, bot)
        await local.ensure_commands(CHAT, 42)
        await GroupService(settings, session).note_group_permissions(CHAT, False)
        assert (await session.get(GroupModerationSettings, CHAT)).local_auto_enabled is False
        with pytest.raises(DomainError, match="group_unavailable"):
            await local._check_group(CHAT)


async def test_postgres_concurrent_first_commands_enrol_one_local_group(
    postgres_contract, settings
):
    import asyncio

    from app.group_services import GroupService
    from app.models import GroupModerationSettings, ManagedGroup, ObservedMember
    from app.moderation_services import GroupModerationService

    factory = postgres_contract[0]
    bot = CheckBot()
    chat = -1009988776655
    bot.admin(chat, 42)
    bot.members[chat, bot.id] = bot.peer(
        bot.id, "administrator", is_bot=True, can_restrict_members=True, can_change_info=True
    )

    async def enrol():
        async with factory() as session:
            await GroupModerationService(session, bot).ensure_commands(chat, 42)

    await asyncio.gather(enrol(), enrol())
    async with factory() as session:
        group = await session.get(ManagedGroup, chat)
        assert not group.approved and not group.enabled
        assert (await session.get(GroupModerationSettings, chat)).local_auto_enabled
        await GroupService(settings, session).observe_member(chat, 51, "target_user", "Actual name")
        assert (await session.get(ObservedMember, (chat, 51))).username == "target_user"
        await GroupService(settings, session).disable_group(chat)
        assert not (await session.get(GroupModerationSettings, chat)).local_auto_enabled


@pytest.mark.parametrize("explicit", [False, True])
async def test_command_defaults_allow_permanent_without_overwriting_explicit_duration_limits(
    database, explicit
):
    from app.models import GroupModerationSettings
    from app.moderation_services import GroupModerationService

    bot = CheckBot()
    await setup_groups(database, bot)
    async with database() as session:
        session.add(
            GroupModerationSettings(
                chat_id=CHAT,
                updated_by=42 if explicit else None,
                allow_permanent_bans=False,
                allow_permanent_mutes=False,
                max_duration_seconds=604800,
            )
        )
        await session.commit()
        await GroupModerationService(session, bot).ensure_commands(CHAT, 42)
        policy = await session.get(GroupModerationSettings, CHAT)
        assert policy.allow_permanent_bans is (not explicit)
        assert policy.allow_permanent_mutes is (not explicit)
        assert policy.max_duration_seconds == 604800

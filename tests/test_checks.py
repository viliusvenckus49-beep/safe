"""Real SQL/routing and stateful Telegram doubles. No live native moderation."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import User
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, OTHER, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey

from app.bot.checks_runtime import inspect_join, process_checks, security_text
from app.bot.checks_ui import cb
from app.bot.local_moderation import LocalModerationExecutor
from app.checks import configure, observe
from app.errors import DomainError
from app.i18n import use_language
from app.models import GroupCheckEvent, GroupCheckSettings, GroupSanction, now
from app.services import Service

pytestmark = pytest.mark.asyncio


class CheckBot(NativeBot):
    def __init__(self):
        super().__init__()
        self.notices = []
        self.edits = []
        self.deletions = []
        self.photo_count = 1
        self.photo_error = None
        self.notice_error = None
        self.sequence = 800
        self.person()
        for chat in (CHAT, OTHER):
            self.members[chat, self.id].can_delete_messages = True

    def person(
        self,
        *,
        chat=CHAT,
        identifier=51,
        name="yiye",
        username=None,
        last_name=None,
        status="member",
    ):
        user = User(
            id=identifier, is_bot=False, first_name=name, last_name=last_name, username=username
        )
        self.members[chat, identifier] = SimpleNamespace(user=user, status=status)
        return user

    async def send_message(self, chat_id, text, **kwargs):
        self.notices.append((chat_id, text, kwargs))
        if self.notice_error:
            raise self.notice_error
        self.sequence += 1
        return SimpleNamespace(message_id=self.sequence)

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return True

    async def delete_message(self, chat_id, message_id):
        self.deletions.append((chat_id, message_id))
        return True

    async def get_user_profile_photos(self, *args, **kwargs):
        if self.photo_error:
            raise self.photo_error
        return SimpleNamespace(total_count=self.photo_count)


async def settings_for(factory, bot, *, chat=CHAT, version=None, **values):
    async with factory() as session:
        policy = await session.get(GroupCheckSettings, chat)
        return await configure(
            session,
            bot,
            chat,
            42,
            version if version is not None else (policy.version if policy else 0),
            **values,
        )


async def events(factory):
    async with factory() as session:
        return list(await session.scalars(select(GroupCheckEvent).order_by(GroupCheckEvent.id)))


async def due(factory):
    async with factory() as session:
        for event in await session.scalars(
            select(GroupCheckEvent).where(GroupCheckEvent.state == "WAITING")
        ):
            event.available_at = now() - timedelta(seconds=1)
        await session.commit()


async def prepared(factory, action="KICK", **changes):
    bot = CheckBot()
    await enabled(factory, bot)
    await settings_for(factory, bot, actions={"username": action}, **changes)
    return bot


@pytest.mark.parametrize("action", ["ADVISE", "WARN", "KICK", "MUTE", "BAN"])
async def test_join_notice_precedes_native_action_by_durable_grace(database, action):
    bot = await prepared(database, action)
    user = bot.person(name="<yiye>&")
    await observe(database, bot, CHAT, user, joined_at=now(), check_join=True)
    await process_checks(database, bot)
    row = (await events(database))[0]
    assert len(bot.notices) == 1 and not bot.calls
    text = bot.notices[0][1]
    assert "<b>&lt;yiye&gt;&amp;</b>" in text and "[<code>51</code>]" in text
    assert bot.notices[0][2]["parse_mode"] == "HTML"
    if action == "ADVISE":
        assert row.state == "DONE"
        await process_checks(database, bot)
        assert not bot.calls and len(bot.notices) == 1
        return
    assert row.state == "WAITING" and row.notice_message_id
    assert 13 < (row.available_at.replace(tzinfo=now().tzinfo) - now()).total_seconds() <= 15
    await process_checks(database, bot)
    assert not bot.calls and len(bot.notices) == 1
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0
    # A new worker instance, backed by the persisted deadline (no real 15s sleep).
    await due(database)
    await process_checks(database, bot)
    row = (await events(database))[0]
    assert row.state == "QUEUED" and row.sanction_id
    await LocalModerationExecutor(database, bot).process(limit=2, sanction_id=row.sanction_id)
    await process_checks(database, bot)
    row = (await events(database))[0]
    assert row.state == "DONE"
    if action == "WARN":
        assert not bot.calls
    elif action == "KICK":
        assert [c[0] for c in bot.calls] == ["ban", "unban"]
    else:
        assert [c[0] for c in bot.calls] == ["restrict" if action == "MUTE" else "ban"]
    assert len(bot.notices) == 1 and len(bot.edits) == 1
    assert "<b>&lt;yiye&gt;&amp;</b>" in bot.edits[0][0]
    await process_checks(database, bot)
    assert len(bot.notices) == 1


async def test_exact_requested_lithuanian_kicked_card_uses_real_escaped_identity():
    event = GroupCheckEvent(
        telegram_id=8733410106, display_name="yiye <&>", rule="username", action="KICK"
    )
    with use_language("lt"):
        assert security_text(event) == (
            "🛡 <b>REDSAFE SECURITY</b>\n\n"
            "👤 <b>yiye &lt;&amp;&gt;</b> [<code>8733410106</code>]\n\n"
            "⚠️ <b>Priežastis:</b> Nenustatytas @username.\n"
            "🚫 <b>Veiksmas:</b> <b>KICKED</b>\n\n"
            "<i>Norėdami prisijungti, pirmiausia nustatykite Telegram naudotojo vardą.</i>"
        )


@pytest.mark.parametrize("stage", ["grace", "queued"])
async def test_setting_username_during_grace_or_before_execution_cancels(database, stage):
    bot = await prepared(database)
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    if stage == "queued":
        await due(database)
        await process_checks(database, bot)
    bot.person(username="valid_name")
    if stage == "grace":
        await due(database)
        await process_checks(database, bot)
    else:
        await LocalModerationExecutor(database, bot).process()
        await process_checks(database, bot)
    assert not bot.calls and len(bot.notices) == 1


async def test_checks_settings_version_cancels_only_check_work(database):
    bot = await prepared(database)
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    await settings_for(database, bot, actions={"username": "OFF"})
    await due(database)
    await process_checks(database, bot)
    assert not bot.calls and (await events(database))[0].state == "CANCELLED"
    with pytest.raises(DomainError, match="checks_stale"):
        await settings_for(database, bot, version=0, actions={"username": "BAN"})


@pytest.mark.parametrize(
    "error",
    [TimeoutError(), TelegramRetryAfter(method="sendMessage", message="wait", retry_after=30)],
)
async def test_unconfirmed_notice_never_authorizes_native_action(database, error):
    bot = await prepared(database)
    bot.notice_error = error
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    await due(database)
    await process_checks(database, bot)
    assert not bot.calls
    assert (await events(database))[0].state in {"UNCERTAIN", "PENDING"}
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0


@pytest.mark.parametrize(
    "rule,value",
    [
        ("surname", None),
        ("photo", None),
        ("arabic", "محمد"),
        ("han", "名字"),
        ("cyrillic", "Иван"),
        ("spam", "VIP SALE"),
    ],
)
async def test_obligation_and_script_rules_create_real_user_events(database, rule, value):
    bot = CheckBot()
    await enabled(database, bot)
    await settings_for(
        database, bot, actions={rule: "KICK"}, spam_terms=["sale"] if rule == "spam" else []
    )
    bot.photo_count = 0
    user = bot.person(name=value or "Actual member")
    await observe(database, bot, CHAT, user, check_join=True, joined_at=now())
    rows = await events(database)
    assert len(rows) == 1 and rows[0].rule == rule and rows[0].display_name == user.full_name


async def test_unknown_photo_and_off_checks_never_punish(database):
    bot = await prepared(database, "OFF")
    await observe(database, bot, CHAT, bot.person())
    assert not await events(database)
    await settings_for(database, bot, actions={"photo": "BAN"})
    bot.photo_error = TimeoutError()
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    assert (await events(database))[0].result == "EVIDENCE_UNAVAILABLE"
    assert not bot.notices and not bot.calls


async def test_repeated_messages_deduplicate_advice_and_delete_each_message(database):
    bot = await prepared(database, "ADVISE", delete_messages=True)
    for identifier in (10, 10, 11):
        await observe(database, bot, CHAT, bot.person(), message_id=identifier)
    await process_checks(database, bot)
    assert len(bot.notices) == 1 and sorted(bot.deletions) == [(CHAT, 10), (CHAT, 11)]
    bot.person(username="valid_name")
    await observe(database, bot, CHAT, bot.members[CHAT, 51].user, message_id=12)
    await observe(database, bot, CHAT, bot.person(), message_id=13)
    await process_checks(database, bot)
    assert len(bot.notices) == 2


async def test_only_strongest_rule_punishes_and_off_join_does_not_trigger(database):
    bot = await prepared(database)
    await settings_for(
        database, bot, actions={"username": "KICK", "surname": "BAN"}, check_on_join=False
    )
    await observe(database, bot, CHAT, bot.person(), check_join=True, joined_at=now())
    assert not await events(database)
    await observe(database, bot, CHAT, bot.person(), message_id=1)
    await process_checks(database, bot)
    assert len(bot.notices) == 1 and "BANNED" in bot.notices[0][1]
    assert [e.rule for e in await events(database) if e.state == "WAITING"] == ["surname"]


async def test_live_admins_and_bots_exempt_and_groups_are_independent(database):
    bot = await prepared(database)
    await observe(database, bot, CHAT, User(id=42, is_bot=False, first_name="Admin"))
    await observe(database, bot, OTHER, bot.person(chat=OTHER))
    await observe(database, bot, CHAT, User(id=52, is_bot=True, first_name="Bot"))
    assert not await events(database)
    with pytest.raises(DomainError):
        async with database() as session:
            await configure(session, bot, CHAT, 900, 1, actions={"username": "BAN"})


async def test_quiet_completion_keeps_one_notice(database):
    bot = await prepared(database, "BAN", notify_result=False)
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    await due(database)
    await process_checks(database, bot)
    await LocalModerationExecutor(database, bot).process()
    await process_checks(database, bot)
    assert len(bot.notices) == 1 and not bot.edits and (await events(database))[0].state == "DONE"


async def test_membership_updates_only_count_real_join_transition(database):
    bot = await prepared(database)
    user = bot.person()
    event = SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=CHAT),
        date=now(),
        new_chat_member=SimpleNamespace(status="member", user=user),
        old_chat_member=SimpleNamespace(status="member"),
    )
    await inspect_join(event, database)
    assert not await events(database)
    event.old_chat_member.status = "left"
    await inspect_join(event, database)
    assert len(await events(database)) == 1


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_private_ui_expands_six_actions_and_edits_same_message(
    local_journey, database, settings, lang
):
    journey, native = local_journey
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await journey.click(cb("root", CHAT), actor=42)
    await journey.click(cb("obligations", CHAT), actor=42)
    await journey.click(cb("rule", CHAT, "username"), actor=42)
    menu = next(c for c in reversed(journey.transport.calls) if isinstance(c, EditMessageText))
    rows = menu.reply_markup.inline_keyboard
    expanded = next(
        i for i, row in enumerate(rows) if "Username" in row[0].text or "@username" in row[0].text
    )
    assert [len(rows[expanded + i]) for i in (1, 2)] == [3, 3]
    sent = len([c for c in journey.transport.calls if isinstance(c, SendMessage)])
    await journey.click(cb("set", CHAT, "username.KICK", 0), actor=42)
    await journey.click(cb("root", CHAT), actor=42)
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == sent
    async with database() as session:
        assert (await session.get(GroupCheckSettings, CHAT)).actions == {"username": "KICK"}
    assert not native.calls


async def test_postgres_competing_checks_do_not_duplicate_notices(postgres_contract):
    import asyncio

    factory, _ = postgres_contract
    bot = await prepared(factory)
    user = bot.person()
    await asyncio.gather(observe(factory, bot, CHAT, user), observe(factory, bot, CHAT, user))
    await asyncio.gather(process_checks(factory, bot), process_checks(factory, bot))
    assert len(bot.notices) == 1 and len(await events(factory)) == 1


async def test_queued_cancellation_updates_notice_and_records_skipped_reason(database):
    bot = await prepared(database)
    await observe(database, bot, CHAT, bot.person())
    await process_checks(database, bot)
    bot.person(username="fixed_name")
    await due(database)
    await process_checks(database, bot)
    row = (await events(database))[0]
    assert row.state == "SKIPPED" and row.result == "checks_unconfirmed"
    assert bot.edits and not bot.calls


async def test_interrupted_notice_is_uncertain_and_not_resent(database):
    bot = await prepared(database)
    await observe(database, bot, CHAT, bot.person())
    async with database() as session:
        row = await session.scalar(select(GroupCheckEvent))
        row.state = "SENDING"
        row.updated_at = now() - timedelta(seconds=40)
        await session.commit()
    await process_checks(database, bot)
    assert (await events(database))[0].state == "UNCERTAIN"
    assert not bot.notices and not bot.calls


async def test_bot_rights_and_invalid_configuration_are_enforced(database):
    bot = await prepared(database, "OFF")
    bot.members[CHAT, bot.id].can_delete_messages = False
    with pytest.raises(DomainError, match="group_moderation_bot_rights"):
        await settings_for(database, bot, delete_messages=True)
    with pytest.raises(DomainError, match="checks_terms_required"):
        await settings_for(database, bot, actions={"spam": "BAN"})
    for action in ("UNKNOWN", None, 15):
        with pytest.raises(DomainError, match="checks_invalid"):
            await settings_for(database, bot, actions={"username": action})


async def test_postgres_two_deadline_workers_submit_one_sanction(postgres_contract):
    import asyncio

    factory, _ = postgres_contract
    bot = await prepared(factory, "BAN")
    await observe(factory, bot, CHAT, bot.person())
    await process_checks(factory, bot)
    await due(factory)
    await asyncio.gather(process_checks(factory, bot), process_checks(factory, bot))
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 1
    await asyncio.gather(
        LocalModerationExecutor(factory, bot).process(),
        LocalModerationExecutor(factory, bot).process(),
    )
    assert len(bot.calls) == 1

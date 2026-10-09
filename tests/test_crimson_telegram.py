"""CRIMSON journeys exercise real routing, durable bans and honest Telegram receipts."""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    BanChatMember,
    EditMessageText,
    SendMessage,
    SendPhoto,
)
from aiogram.types import Chat, ChatMemberLeft, ChatMemberMember, ChatMemberUpdated, Message, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from test_telegram import Journey, Transport

from app.bot.callbacks import Action, ScamAdmin
from app.bot.group_keyboards import GroupAction
from app.bot.handlers import create_router
from app.bot.states import RepVoteFlow, ScamAdminFlow
from app.group_services import GroupService
from app.models import (
    BanAction,
    ManagedGroup,
    ReputationAdjustment,
    ReputationRequest,
    ScamRecord,
    TrustedDesignation,
)
from app.services import Service

BRAND = "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™"
BLOCKED = "𝗦𝗖𝗔𝗠 • 𝗕𝗟𝗢𝗖𝗞𝗘𝗗"
REGISTERED = "𝗦𝗖𝗔𝗠 • 𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗"
PROTECTED = (-1001, -1002, -1003)


class ModerationTransport(Transport):
    """A ban really returns bool, unlike the generic UI fixture's Message response."""

    def __init__(self):
        super().__init__()
        self.outcomes = {}

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, BanChatMember):
            self.calls.append(method)
            outcome = self.outcomes.get(int(method.chat_id), True)
            if outcome == "bad_request":
                raise TelegramBadRequest(method=method, message="USER_NOT_PARTICIPANT")
            if outcome == "forbidden":
                raise TelegramForbiddenError(method=method, message="Not enough rights to ban")
            return outcome
        return await super().make_request(bot, method, timeout)

    def bans(self):
        return [
            (int(call.chat_id), call.user_id)
            for call in self.calls
            if isinstance(call, BanChatMember)
        ]


class CrimsonJourney(Journey):
    async def send_user(self, user, text=None, *, chat=None, **extra):
        self.sequence += 1
        message = Message(
            message_id=self.sequence,
            date=datetime.now(UTC),
            chat=Chat(id=chat or user.id, type="private" if chat is None else "supergroup"),
            from_user=user,
            text=text,
            **extra,
        )
        await self.dp.feed_update(self.bot, Update(update_id=self.sequence, message=message))

    def last_text(self):
        return next(
            (getattr(call, "text", None) or getattr(call, "caption", None) or "")
            for call in reversed(self.transport.calls)
            if isinstance(call, (SendMessage, EditMessageText, SendPhoto))
        )


@pytest_asyncio.fixture
async def crimson(database, settings):
    async with database() as session:
        service = Service(settings, session)
        for actor in (1, 2, 900):
            await service.set_language(actor, "lt")
    transport = ModerationTransport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    yield CrimsonJourney(bot, dispatcher, transport)
    await dispatcher.storage.close()
    await bot.session.close()


async def protect_groups(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        for chat_id in PROTECTED:
            await groups.register_group(900, chat_id, f"Group {chat_id}", True)
        # Merely adding the bot does not give permission to protect this group.
        await groups.stage_group(900, -1004, "Awaiting approval")
        await groups.register_group(900, -1005, "Disabled protection", True)
        await groups.disable_group(-1005)


async def requests_count(database):
    async with database() as session:
        return await session.scalar(select(func.count()).select_from(ReputationRequest))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "lang,explanation",
    [
        ("lt", "Vartotojui suteiktas"),
        ("en", "The user received"),
        ("ru", "Пользователю присвоен"),
    ],
)
async def test_trusted_receipt_preserves_actual_identity(
    crimson, database, settings, lang, explanation
):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        await core.observe(78654739, "plain_name", "Živilė Яна")
    await crimson.send("/add_trusted 78654739", actor=900)
    receipt = crimson.last_text()
    assert "𝗧𝗥𝗨𝗦𝗧𝗘𝗗 • 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗" in receipt
    assert "@plain_name" in receipt and "78654739" in receipt
    assert explanation in receipt and BRAND in receipt
    assert not crimson.transport.bans()
    async with database() as session:
        profile = await Service(settings, session).profile("78654739")
        assert profile["trusted"] and profile["user"].display_name == "Živilė Яна"


@pytest.mark.asyncio
async def test_trusted_receipt_without_username_keeps_real_name(crimson, database, settings):
    async with database() as session:
        await Service(settings, session).observe(42, None, "Živilė Яна <3")
    await crimson.send("/add_trusted 42", actor=900)
    assert "Živilė Яна &lt;3" in crimson.last_text()
    assert "🆔 <code>42</code>" in crimson.last_text()
    assert "@username" not in crimson.last_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("wizard", [False, True])
async def test_add_scam_known_id_bans_all_active_groups_before_receipt(
    crimson, database, settings, wizard
):
    await protect_groups(database, settings)
    async with database() as session:
        await Service(settings, session).observe(7681768804, "plain_scam", "Normal name")
    if wizard:
        await crimson.send("/add_sc", actor=900)
        await crimson.send("7681768804", actor=900)
    else:
        await crimson.send("/add_sc 7681768804", actor=900)
    assert set(crimson.transport.bans()) == {(chat_id, 7681768804) for chat_id in PROTECTED}
    receipt = crimson.last_text()
    assert BLOCKED in receipt and "@plain_scam" in receipt
    assert "7681768804" in receipt and "3/3" in receipt and BRAND in receipt
    async with database() as session:
        rows = (await session.scalars(select(BanAction))).all()
        assert len(rows) == 3 and all(row.status == "SUCCEEDED" for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_unknown_scam_id_stays_unblocked_then_confirmed_id_bans_immediately(
    crimson, database, settings, lang
):
    await protect_groups(database, settings)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await crimson.send("/add_sc @unresolved_person", actor=900)
    receipt = crimson.last_text()
    assert REGISTERED in receipt and "𝗨𝗡𝗞𝗡𝗢𝗪𝗡" in receipt
    assert BLOCKED not in receipt and "@unresolved_person" in receipt and BRAND in receipt
    assert not crimson.transport.bans()
    assert await crimson.state(900) == ScamAdminFlow.input.state
    async with database() as session:
        record = await session.scalar(select(ScamRecord))
        record_id = record.id
    buttons = crimson.transport.calls[-1].reply_markup.inline_keyboard
    assert any(
        button.callback_data == ScamAdmin(action="id_receipt", value=str(record_id)).pack()
        for row in buttons
        for button in row
    )
    await crimson.send("6961937011", actor=900)
    assert await crimson.state(900) == ScamAdminFlow.preview.state
    assert not crimson.transport.bans()
    confirm = ScamAdmin(action="confirm", value=(await crimson.data(900))["scam_nonce"]).pack()
    await crimson.click(confirm, actor=900)
    assert set(crimson.transport.bans()) == {(chat_id, 6961937011) for chat_id in PROTECTED}
    assert BLOCKED in crimson.last_text() and "3/3" in crimson.last_text()
    assert "@unresolved_person" in crimson.last_text() and "6961937011" in crimson.last_text()
    await crimson.click(confirm, actor=900)
    assert len(crimson.transport.bans()) == 3
    async with database() as session:
        profile = await Service(settings, session).profile("6961937011")
        assert profile["scam"].id == record_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcomes,succeeded,pending,result_types",
    [
        ({-1002: "bad_request"}, 2, 1, {"BANNED", "TelegramBadRequest"}),
        (
            {chat_id: "bad_request" for chat_id in PROTECTED},
            0,
            3,
            {"TelegramBadRequest"},
        ),
        (
            {-1001: "forbidden", -1002: "forbidden", -1003: "forbidden"},
            0,
            3,
            {"TelegramForbiddenError"},
        ),
        ({chat_id: False for chat_id in PROTECTED}, 0, 3, {"API_FALSE"}),
    ],
)
async def test_failed_telegram_bans_are_pending_and_never_reported_blocked(
    crimson, database, settings, outcomes, succeeded, pending, result_types
):
    await protect_groups(database, settings)
    crimson.transport.outcomes.update(outcomes)
    await crimson.send("/add_sc 42", actor=900)
    receipt = crimson.last_text()
    assert REGISTERED in receipt and BLOCKED not in receipt
    assert f"Queued  {pending}" in receipt
    assert "Automatinis blokavimas neaktyvus" not in receipt
    assert "USER_NOT_PARTICIPANT" not in receipt and "Traceback" not in receipt
    if succeeded:
        assert f"{succeeded}/3" in receipt
    async with database() as session:
        record = await session.scalar(select(ScamRecord))
        summary = await GroupService(settings, session).ban_summary(record.id)
        assert (summary.checked, summary.succeeded, summary.failed, summary.pending) == (
            3,
            succeeded,
            pending,
            pending,
        )
        rows = (await session.scalars(select(BanAction))).all()
        assert {row.result_type for row in rows} == result_types
        assert sum(row.status == "SUCCEEDED" for row in rows) == succeeded
        if "forbidden" in outcomes.values():
            for chat_id in PROTECTED:
                group = await session.get(ManagedGroup, chat_id)
                assert group.approved and group.enabled and not group.can_restrict_members


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["message", "service_join", "chat_member"])
async def test_pending_scam_presence_retries_without_posting_menus(
    crimson, database, settings, trigger
):
    await protect_groups(database, settings)
    crimson.transport.outcomes[-1001] = "bad_request"
    await crimson.send("/add_sc 42", actor=900)
    crimson.transport.calls.clear()
    crimson.transport.outcomes.clear()
    target = TelegramUser(id=42, is_bot=False, first_name="Person", username="renamed_person")
    if trigger == "message":
        await crimson.send_user(target, "ordinary conversation", chat=-1001)
    elif trigger == "service_join":
        await crimson.send(None, actor=3, chat=-1001, new_chat_members=[target])
    else:
        crimson.sequence += 1
        event = ChatMemberUpdated(
            chat=Chat(id=-1001, type="supergroup"),
            from_user=TelegramUser(id=3, is_bot=False, first_name="Inviter"),
            date=datetime.now(UTC),
            old_chat_member=ChatMemberLeft(user=target),
            new_chat_member=ChatMemberMember(user=target),
        )
        await crimson.dp.feed_update(
            crimson.bot, Update(update_id=crimson.sequence, chat_member=event)
        )
    assert crimson.transport.bans() == [(-1001, 42)]
    assert not any(
        isinstance(call, (SendMessage, SendPhoto, EditMessageText))
        for call in crimson.transport.calls
    )
    async with database() as session:
        row = await session.scalar(select(BanAction).where(BanAction.chat_id == -1001))
        assert row.status == "SUCCEEDED" and row.result_type == "BANNED"
        profile = await Service(settings, session).profile("42")
        assert profile["scam"] is not None and profile["user"].username == "renamed_person"


@pytest.mark.asyncio
async def test_observed_username_coincidence_never_binds_scam_to_new_owner(
    crimson, database, settings
):
    await protect_groups(database, settings)
    await crimson.send("/add_sc @reassigned_name", actor=900)
    async with database() as session:
        original_record = await session.scalar(select(ScamRecord))
        record_id = original_record.id
    crimson.transport.calls.clear()
    new_owner = TelegramUser(
        id=42, is_bot=False, username="reassigned_name", first_name="New owner"
    )
    await crimson.send_user(new_owner, "ordinary conversation", chat=-1001)
    assert not crimson.transport.bans()
    async with database() as session:
        record = await session.get(ScamRecord, record_id)
        unresolved = await Service(settings, session).resolve(f"u:{record.target_id}")
        known_profile = await Service(settings, session).profile("42")
        assert unresolved.telegram_id is None and record.status == "ACTIVE"
        assert known_profile["scam"] is None
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0


@pytest.mark.asyncio
async def test_existing_numeric_scam_observation_bans_every_group_without_changing_record(
    crimson, database, settings
):
    await protect_groups(database, settings)
    async with database() as session:
        core = Service(settings, session)
        user = await core.observe(42, "previous_name", "Previous name")
        await core.admin_adjust_rep(900, "42", 7, "Previously reviewed reputation", "previous-rep")
        await core.set_trusted(900, "42", True, "previous-trusted")
        # An imported existing record can have no queued delivery rows yet.
        record = ScamRecord(target_id=user.id, moderator_id=900, status="ACTIVE", reason="")
        session.add(record)
        await session.commit()
        record_id = record.id
    await crimson.send_user(
        TelegramUser(
            id=42, is_bot=False, username="current_name", first_name="Current", last_name="Name"
        ),
        "/profile",
    )
    assert set(crimson.transport.bans()) == {(chat_id, 42) for chat_id in PROTECTED}
    async with database() as session:
        profile = await Service(settings, session).profile("42")
        assert profile["scam"].id == record_id
        assert profile["user"].telegram_id == 42 and profile["user"].username == "current_name"
        assert profile["user"].display_name == "Current Name"
        assert profile["score"] == 7
        assert await session.scalar(select(func.count()).select_from(ReputationAdjustment)) == 1
        assert (await session.scalar(select(TrustedDesignation))).active


@pytest.mark.asyncio
@pytest.mark.parametrize("sign,actor,target,label", [(1, 1, 42, "+ʀᴇᴘ"), (-1, 2, 43, "-ʀᴇᴘ")])
async def test_result_rep_buttons_require_five_character_comment_and_preserve_value(
    crimson, database, settings, sign, actor, target, label
):
    await crimson.send(f"/ask {target}", actor=actor)
    buttons = crimson.transport.calls[-1].reply_markup.inline_keyboard
    button = next(button for row in buttons for button in row if button.text == label)
    callback = Action.unpack(button.callback_data)
    assert callback.name == ("vote+" if sign == 1 else "vote-")
    await crimson.click(button.callback_data, actor=actor)
    assert await crimson.state(actor) == RepVoteFlow.comment.state
    await crimson.send("four", actor=actor)
    assert await requests_count(database) == 0
    assert await crimson.state(actor) == RepVoteFlow.comment.state
    await crimson.send("valid", actor=actor)
    assert await crimson.state(actor) is None
    async with database() as session:
        request = await session.scalar(select(ReputationRequest))
        assert request.value == sign and request.comment == "valid" and request.status == "PENDING"
        assert (await Service(settings, session).profile(str(target)))["score"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_main_menu_callback_contract_survives_crimson_labels(
    crimson, database, settings, lang
):
    async with database() as session:
        await Service(settings, session).set_language(1, lang)
    await crimson.send("/start")
    markup = crimson.transport.calls[-1].reply_markup
    rows = [[button.callback_data for button in row] for row in markup.inline_keyboard]
    assert rows == [
        ["sc|lookup|"],
        ["sc|profile|", "sc|report|"],
        ["sc|top|", "sc|scams|0"],
        ["sc|rep|", "sc|info|"],
        [GroupAction(action="subscriptions").pack()],
        ["sc|language|"],
        ["sc|close|"],
    ]
    if lang == "lt":
        assert [[button.text for button in row] for row in markup.inline_keyboard] == [
            ["ᴛɪᴋʀɪɴᴛɪ"],
            ["ᴘʀᴏꜰɪʟɪꜱ", "ᴘʀᴀɴᴇꜱᴛɪ"],
            ["ᴛᴏᴘ 10", "ꜱᴄᴀᴍᴇʀɪᴀɪ"],
            ["ɪᴠᴇʀᴛɪɴᴛɪ", "ɪɴꜰᴏʀᴍᴀᴄɪᴊᴀ"],
            ["ɢʀᴜᴘᴇꜱ ᴀᴛᴋᴜʀɪᴍᴀꜱ"],
            ["ᴋᴀʟʙᴀ"],
            ["ᴜᴢᴅᴀʀʏᴛɪ"],
        ]
    for callback in [value for row in rows[:-1] for value in row]:
        before = len(crimson.transport.calls)
        await crimson.click(callback)
        calls = crimson.transport.calls[before:]
        assert any(isinstance(call, AnswerCallbackQuery) for call in calls)
        assert any(isinstance(call, (SendMessage, SendPhoto, EditMessageText)) for call in calls)
        assert all(not call.show_alert for call in calls if isinstance(call, AnswerCallbackQuery))
    await crimson.click("sc|home|")
    assert BRAND in crimson.last_text()
    await crimson.click("sc|close|")
    assert await crimson.state() is None and crimson.transport.deletions

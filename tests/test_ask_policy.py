"""Persistent lookup results and quiet, gradually replenished user budgets."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from test_telegram import journey as telegram_journey

from app.bot import keyboards as kb
from app.bot.callbacks import Action
from app.bot.lookup_limits import AskLimiter
from app.bot.middleware import ServiceMiddleware
from app.bot.states import InputFlow, RepVoteFlow
from app.i18n import language
from app.models import ReputationRequest
from app.services import Service

journey = telegram_journey


@pytest.fixture
def clock(monkeypatch):
    current = [1000.0]
    monkeypatch.setattr("app.bot.middleware.monotonic", lambda: current[0])
    return current


def sent(j):
    return [
        (index + 1, call)
        for index, call in enumerate(j.transport.calls)
        if isinstance(call, (SendMessage, SendPhoto))
    ]


async def click_result(j, result, data, *, actor=1, chat=None):
    message_id, call = result
    j.sequence += 1
    query = CallbackQuery(
        id=str(j.sequence),
        from_user=User(id=actor, is_bot=False, first_name="Tester"),
        chat_instance="test",
        data=data,
        message=Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat or actor, type="private" if chat is None else "supergroup"),
            reply_markup=call.reply_markup,
        ),
    )
    await j.dp.feed_update(j.bot, Update(update_id=j.sequence, callback_query=query))


def test_burst_recovers_gradually_even_when_impatient_attempts_continue():
    limiter = AskLimiter()
    for target in range(10):
        assert limiter.admit(1, str(target), 1000) is None
    for second in range(6):
        assert limiter.admit(1, "next", 1000 + second) == "burst"
    assert limiter.admit(1, "next", 1006) is None
    assert limiter.admit(1, "another", 1006) == "burst"
    assert limiter.admit(1, "another", 1012) is None
    for target in range(10):
        assert limiter.admit(1, str(target), 1072) is None


def test_duplicates_use_case_insensitive_targets_without_spending_budget_or_extending_wait():
    limiter = AskLimiter()
    assert limiter.admit(1, "@Example", 1000) is None
    for second in range(10):
        assert limiter.admit(1, "@EXAMPLE", 1000 + second) == "duplicate"
    for target in range(9):
        assert limiter.admit(1, str(target), 1009) is None
    assert limiter.admit(1, "@example", 1010) is None
    assert limiter.admit(2, "@example", 1010) is None


def test_inactive_users_expire_and_memory_is_bounded(monkeypatch):
    monkeypatch.setattr("app.bot.lookup_limits.ASK_MAX_USERS", 3)
    limiter = AskLimiter()
    for actor in range(10):
        assert limiter.admit(actor, "42", 1000) is None
    assert len(limiter._users) == 3
    assert limiter.admit(9, "42", 1120) is None
    assert len(limiter._users) == 1


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("chat", [None, -100])
async def test_results_survive_commands_and_back_in_all_languages(
    journey, database, settings, clock, lang, chat
):
    async with database() as session:
        await Service(settings, session).set_language(1, lang)
    await journey.send("/start", chat=chat)
    panel = sent(journey)[-1][0]
    await journey.send("/ask 42", chat=chat)
    first = sent(journey)[-1]
    assert panel in [call.message_id for call in journey.transport.deletions]
    for command in ("/ask 43", "/profile", "/top", "/start", "/cancel"):
        await journey.send(command, chat=chat)
    await click_result(journey, first, kb.action("home", "receipt"), chat=chat)
    assert first[0] not in [call.message_id for call in journey.transport.deletions]
    assert all(
        b.callback_data != kb.action("close")
        for row in first[1].reply_markup.inline_keyboard
        for b in row
    )


async def test_duplicate_spam_is_silent_and_only_accepted_queries_refresh_identity(
    journey, clock, monkeypatch
):
    original = Service.profile
    calls = []

    async def profile(self, target, *, refresh_identity=False):
        calls.append((target, refresh_identity))
        return await original(self, target, refresh_identity=refresh_identity)

    monkeypatch.setattr(Service, "profile", profile)
    await journey.send("/ask @example")
    first = sent(journey)[-1][0]
    for _ in range(10):
        await journey.send("/ask@redsafecheckbot @EXAMPLE")
    assert len(sent(journey)) == 1
    assert calls == [("@example", True)]
    clock[0] += 10
    await journey.send("/ask @example")
    assert len(sent(journey)) == 2 and len(calls) == 2
    assert first not in [call.message_id for call in journey.transport.deletions]


async def test_budget_is_shared_across_chats_but_not_users_or_commands(journey, clock):
    for target in range(40, 50):
        await journey.send(f"/ask {target}", chat=-100 if target % 2 else None)
    assert len(sent(journey)) == 10
    for _ in range(10):
        await journey.send("/ask 50", chat=-200)
    assert len(sent(journey)) == 10
    await journey.send("/ask 50", actor=2, chat=-200)
    await journey.send("/start")
    assert len(sent(journey)) == 12
    clock[0] += 6
    await journey.send("/ask 50")
    assert len(sent(journey)) == 13
    await journey.send("/ask 51")
    assert len(sent(journey)) == 13


async def test_reply_and_explicit_target_share_duplicate_guard(journey, clock):
    reply = Message(
        message_id=99,
        date=datetime.now(UTC),
        chat=Chat(id=-100, type="supergroup"),
        from_user=User(id=42, is_bot=False, first_name="Target"),
    )
    await journey.send("/ask", chat=-100, reply_to_message=reply)
    await journey.send("/ask 42", chat=-100)
    assert len(sent(journey)) == 1
    await journey.send("/ask 43", chat=-100, reply_to_message=reply)
    assert len(sent(journey)) == 2


@pytest.mark.parametrize("chat", [None, -100])
async def test_prompt_and_check_another_preserve_results_and_guard_input(journey, clock, chat):
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=chat or 1, user_id=1)
    await journey.send("/ask", chat=chat)
    await journey.send("42", chat=chat)
    first = sent(journey)[-1]
    await click_result(journey, first, kb.action("lookup"), chat=chat)
    assert await state.get_state() == InputFlow.lookup.state
    before = len(sent(journey))
    await journey.send("42", chat=chat)
    assert len(sent(journey)) == before
    assert await state.get_state() == InputFlow.lookup.state
    await journey.send("43", chat=chat)
    second = sent(journey)[-1]
    assert await state.get_state() is None
    await journey.send("/profile", chat=chat)
    deleted = [call.message_id for call in journey.transport.deletions]
    assert first[0] not in deleted and second[0] not in deleted


async def test_self_rep_error_does_not_remove_original_lookup(journey, clock):
    await journey.send("/ask 1")
    result = sent(journey)[-1]
    await click_result(journey, result, kb.action("vote+", "1"))
    assert await journey.state() is None
    assert result[0] not in [call.message_id for call in journey.transport.deletions]
    await journey.send("/start")
    assert result[0] not in [call.message_id for call in journey.transport.deletions]


async def test_other_bot_mentions_do_not_spend_safecheck_budget(journey, clock):
    for _ in range(10):
        await journey.send("/ask@anotherbot 42")
    assert not sent(journey)
    for target in range(40, 50):
        await journey.send(f"/ask {target}")
    assert len(sent(journey)) == 10


@pytest.mark.parametrize("command,value", [("+rep", 1), ("-REP", -1)])
async def test_exhausted_lookup_prompt_does_not_throttle_rep_commands(
    journey, database, clock, command, value
):
    for target in range(40, 49):
        await journey.send(f"/ask {target}")
    await journey.send("/ask")
    assert await journey.state() == InputFlow.lookup.state
    await journey.send(f"{command} 42 Reviewed useful trade feedback")
    async with database() as session:
        request = await session.scalar(select(ReputationRequest))
        assert request is not None and request.value == value
    assert await journey.state() is None


@pytest.mark.parametrize("value", [1, -1])
@pytest.mark.parametrize("chat", [None, -100])
async def test_rep_callbacks_keep_result_and_original_vote_behavior(
    journey, database, clock, value, chat
):
    await journey.send("/ask 42", chat=chat)
    result = sent(journey)[-1]
    await click_result(
        journey, result, kb.action("vote+" if value == 1 else "vote-", "42"), chat=chat
    )
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=chat or 1, user_id=1)
    assert await state.get_state() == RepVoteFlow.comment.state
    prompt_id = (await state.get_data()).get("rep_prompt_id")
    extra = {}
    if chat is not None:
        extra["reply_to_message"] = Message(
            message_id=prompt_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat, type="supergroup"),
        )
    await journey.send("Reviewed useful trade feedback", chat=chat, **extra)
    async with database() as session:
        request = await session.scalar(select(ReputationRequest))
        assert request is not None and request.value == value
    assert result[0] not in [call.message_id for call in journey.transport.deletions]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_general_throttle_also_silences_ask_and_resets_language(
    database, settings, monkeypatch, clock, lang
):
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    response = AsyncMock()
    monkeypatch.setattr(Message, "answer", response)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, first_name="Tester", is_bot=False),
        text="/ask 43",
    )
    middleware = ServiceMiddleware(settings, database)
    middleware.recent[42] = [clock[0]] * 20
    handler = AsyncMock()
    await middleware(handler, message, {})
    handler.assert_not_awaited()
    response.assert_not_awaited()
    assert language.get() == "lt"


@pytest.mark.parametrize("chat", [None, -100])
async def test_check_profile_replaces_source_but_check_another_keeps_history(journey, clock, chat):
    from aiogram.types import ForceReply

    await journey.send("/ask 42", chat=chat)
    original = sent(journey)[-1]
    buttons = original[1].reply_markup.inline_keyboard
    assert [Action.unpack(row[0].callback_data).name for row in buttons] == ["profile", "lookup"]
    await click_result(journey, original, buttons[1][0].callback_data, chat=chat)
    prompt = sent(journey)[-1]
    state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=chat or 1, user_id=1)
    assert await state.get_state() == InputFlow.lookup.state
    assert original[0] not in [call.message_id for call in journey.transport.deletions]
    extra = {}
    if chat is not None:
        assert isinstance(prompt[1].reply_markup, ForceReply)
        assert prompt[1].reply_markup.selective is True
        assert "tg://user?id=1" in prompt[1].text
        extra["reply_to_message"] = Message(
            message_id=prompt[0],
            date=datetime.now(UTC),
            chat=Chat(id=chat, type="supergroup"),
        )
    await journey.send("43", chat=chat, **extra)
    checked = sent(journey)[-1]
    assert "<code>43</code>" in checked[1].text
    assert await state.get_state() is None
    assert original[0] not in [call.message_id for call in journey.transport.deletions]
    await click_result(
        journey, checked, checked[1].reply_markup.inline_keyboard[0][0].callback_data, chat=chat
    )
    deleted = [call.message_id for call in journey.transport.deletions]
    assert checked[0] in deleted and original[0] not in deleted
    profile = sent(journey)[-1][1]
    assert "🆔 ID: 43" in profile.text
    assert [
        Action.unpack(row[0].callback_data).name for row in profile.reply_markup.inline_keyboard
    ] == ["redsafe_names", "redsafe_check"]


@pytest.mark.parametrize("chat", [None, -100])
async def test_names_history_returns_to_same_profile_or_reputation_and_retires_panel(
    journey, clock, chat
):
    await journey.send("/ask 42", chat=chat)
    check = sent(journey)[-1]
    await click_result(
        journey, check, check[1].reply_markup.inline_keyboard[0][0].callback_data, chat=chat
    )
    profile = sent(journey)[-1]
    await click_result(
        journey, profile, profile[1].reply_markup.inline_keyboard[0][0].callback_data, chat=chat
    )
    history = sent(journey)[-1]
    buttons = history[1].reply_markup.inline_keyboard
    assert [Action.unpack(row[0].callback_data).name for row in buttons] == [
        "profile",
        "redsafe_check",
    ]
    assert Action.unpack(buttons[0][0].callback_data).value == "42"
    await click_result(journey, history, buttons[0][0].callback_data, chat=chat)
    restored = sent(journey)[-1]
    assert "🆔 ID: 42" in restored[1].text
    assert history[0] in [call.message_id for call in journey.transport.deletions]
    await click_result(
        journey, restored, restored[1].reply_markup.inline_keyboard[1][0].callback_data, chat=chat
    )
    restored_check = sent(journey)[-1]
    assert "<code>42</code>" in restored_check[1].text
    assert restored[0] in [call.message_id for call in journey.transport.deletions]
    await click_result(
        journey,
        restored_check,
        restored_check[1].reply_markup.inline_keyboard[0][0].callback_data,
        chat=chat,
    )
    next_profile = sent(journey)[-1]
    await click_result(
        journey,
        next_profile,
        next_profile[1].reply_markup.inline_keyboard[0][0].callback_data,
        chat=chat,
    )
    next_history = sent(journey)[-1]
    await click_result(
        journey,
        next_history,
        next_history[1].reply_markup.inline_keyboard[1][0].callback_data,
        chat=chat,
    )
    assert "<code>42</code>" in sent(journey)[-1][1].text
    assert next_history[0] in [call.message_id for call in journey.transport.deletions]

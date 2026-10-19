"""Private schedule navigation and public callbacks bound to real delivered parts."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetChatMember
from aiogram.types import CallbackQuery, Chat, ChatMemberOwner, Message, Update, User
from test_ads_publication import clock as clock
from test_ads_publication import configured
from test_telegram import journey as journey

from app.bot.ads_publisher import AdsPublisher
from app.bot.ads_schedule import cb
from app.bot.callbacks import PublishedAd
from app.i18n import t
from app.models import AdSchedule
from app.services import Service


@pytest.fixture
def members(journey, monkeypatch):
    original = journey.transport.make_request

    async def request(bot, method, timeout=None):
        if isinstance(method, GetChatMember):
            journey.transport.calls.append(method)
            return ChatMemberOwner(
                user=User(id=method.user_id, is_bot=method.user_id == bot.id, first_name="Admin"),
                is_anonymous=False,
            )
        return await original(bot, method, timeout)

    monkeypatch.setattr(journey.transport, "make_request", request)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_schedule_menu_inputs_and_confirm_in_every_language(
    journey, database, settings, clock, members, lang
):
    cid, sid, _, _, _ = await configured(database, settings, clock)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.click(cb("panel", cid), actor=900)
    panels = [call for call in journey.transport.calls if isinstance(call, EditMessageText)]
    assert "&lt;secret&gt;" in panels[-1].text and "&lt;&amp;&gt;" in panels[-1].text
    assert t("ap.title", lang) in panels[-1].text
    await journey.click(cb("intervals", cid), actor=900)
    markup = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1].reply_markup
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == 10
    await journey.click(buttons[2].callback_data, actor=900)
    async with database() as session:
        row = await session.get(AdSchedule, sid)
        version = row.version
        assert row.status == "PAUSED" and row.interval_seconds == 7200
    await journey.click(cb("edit", cid, f"{version}.start_time"), actor=900)
    await journey.send("13:00", actor=900)
    async with database() as session:
        row = await session.get(AdSchedule, sid)
        assert row.start_at.replace(tzinfo=UTC).hour == 11
    await journey.click(cb("start", cid), actor=900)
    confirmation = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]
    assert t("ap.confirm_button", lang) == confirmation.reply_markup.inline_keyboard[0][0].text
    payload = confirmation.reply_markup.inline_keyboard[0][0].callback_data
    await journey.click(payload, actor=900)
    await journey.click(payload, actor=900)
    async with database() as session:
        row = await session.get(AdSchedule, sid)
        assert row.status == "ACTIVE"
        version = row.version
    await journey.click(cb("pause", cid, str(version)), actor=900)
    assert t("ap.control_note", lang) in journey.text()


@pytest.mark.parametrize(
    "action",
    [
        "panel",
        "intervals",
        "calendar",
        "groups",
        "start",
        "confirm",
        "pause",
        "stop",
        "stats",
        "history",
        "edit",
        "interval",
        "group",
    ],
)
async def test_nonadmin_cannot_forge_any_management_callback(
    journey, database, settings, clock, action
):
    cid, sid, _, _, _ = await configured(database, settings, clock)
    async with database() as session:
        original = (await session.get(AdSchedule, sid)).version
    await journey.click(cb(action, cid, "1"), actor=1)
    async with database() as session:
        row = await session.get(AdSchedule, sid)
        assert row.version == original and row.status == "ACTIVE"


async def public_click(journey, payload, message_id, chat=-100):
    journey.sequence += 1
    await journey.dp.feed_update(
        journey.bot,
        Update(
            update_id=journey.sequence,
            callback_query=CallbackQuery(
                id=str(journey.sequence),
                from_user=User(id=1, is_bot=False, first_name="Reader"),
                chat_instance="public",
                data=payload,
                message=Message(
                    message_id=message_id,
                    date=datetime.now(UTC),
                    chat=Chat(id=chat, type="supergroup"),
                ),
            ),
        ),
    )
    return [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][-1]


async def test_public_popup_needs_exact_delivered_keyboard_message(
    journey, database, settings, clock
):
    cid, sid, bot, _, _ = await configured(database, settings, clock, album=True)
    await AdsPublisher(bot, settings, database).tick()
    markup = bot.calls[-1][1]["reply_markup"]
    payload = markup.inline_keyboard[0][0].callback_data
    assert PublishedAd.unpack(payload).attempt > 0
    assert (await public_click(journey, payload, 102)).text == "Original popup"
    assert (
        await public_click(journey, payload, 100)
    ).text != "Original popup"  # Album has no keyboard.
    assert (await public_click(journey, payload, 999)).text != "Original popup"
    assert (await public_click(journey, payload, 102, chat=-200)).text != "Original popup"


async def test_publication_history_can_reach_older_retained_attempts(
    journey, database, settings, clock
):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    worker = AdsPublisher(bot, settings, database)
    for _ in range(6):
        await worker.tick()
        clock[0] += timedelta(minutes=30)
    await journey.click(cb("history", cid), actor=900)
    first = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]
    assert "#6 " in first.text and "#1 " not in first.text
    next_button = first.reply_markup.inline_keyboard[0][0]
    await journey.click(next_button.callback_data, actor=900)
    second = [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]
    assert "#1 " in second.text and "#6 " not in second.text


async def test_schedule_navigation_closes_sql_transactions_before_telegram(
    journey, database, settings, clock, members, monkeypatch
):
    import app.bot.ads_schedule as module

    cid, sid, _, _, _ = await configured(database, settings, clock)
    observed = []
    original_private = module.private
    original_request = journey.transport.make_request

    async def private(service, message, actor):
        result = await original_private(service, message, actor)
        observed.append(service.session)
        return result

    async def request(bot, method, timeout=None):
        if observed and isinstance(method, (AnswerCallbackQuery, EditMessageText, GetChatMember)):
            assert not observed[-1].in_transaction()
        return await original_request(bot, method, timeout)

    monkeypatch.setattr(module, "private", private)
    monkeypatch.setattr(journey.transport, "make_request", request)
    for action in ["panel", "intervals", "calendar", "groups", "start", "stats", "history"]:
        await journey.click(cb(action, cid), actor=900)
    assert len(observed) == 7

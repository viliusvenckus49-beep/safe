"""Real aiogram routing/FSM journeys; no Telegram API calls or production data."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from sqlalchemy import select
from test_telegram import journey as telegram_journey

from app.bot import merchant_views as views
from app.bot.callbacks import Merchant
from app.bot.states import MerchantFlow
from app.i18n import CATALOGS, use_language
from app.merchant_services import MerchantService
from app.models import MerchantEntry, MerchantProposal, ReputationRequest
from app.services import Service

journey = telegram_journey


def screen(j):
    for index, call in reversed(list(enumerate(j.transport.calls, 1))):
        if isinstance(call, (SendMessage, EditMessageText, SendPhoto)):
            return (call.message_id if isinstance(call, EditMessageText) else index), call
    raise AssertionError("No screen")


async def tap(j, action=None, *, actor=1, value=None, forged=None):
    panel, call = screen(j)
    data = forged
    if data is None:
        found = []
        for row in call.reply_markup.inline_keyboard:
            for b in row:
                if b.callback_data and b.callback_data.startswith("mt|"):
                    cb = Merchant.unpack(b.callback_data)
                    if cb.action == action and (value is None or cb.value == value):
                        found.append(b.callback_data)
        assert found, (action, value, call.reply_markup)
        data = found[0]
    j.sequence += 1
    cb = CallbackQuery(
        id=str(j.sequence),
        from_user=User(id=actor, is_bot=False, first_name="Tester"),
        chat_instance="merchant-test",
        data=data,
        message=Message(
            message_id=panel,
            date=datetime.now(UTC),
            chat=Chat(id=actor, type="private"),
            text=getattr(call, "text", None),
            reply_markup=call.reply_markup,
        ),
    )
    await j.dp.feed_update(j.bot, Update(update_id=j.sequence, callback_query=cb))


async def initial_menu(j, actor=1):
    await j.send("/start", actor=actor)
    anchor, call = screen(j)
    if any(
        b.callback_data == "language:lt" for row in call.reply_markup.inline_keyboard for b in row
    ):
        await j.click("language:lt", actor=actor, message_id=anchor)


async def open_catalog(j, actor=1):
    await initial_menu(j, actor)
    await tap(j, "root", actor=actor)


async def open_admin_catalog(j):
    await initial_menu(j, 900)
    anchor, _ = screen(j)
    await j.click("sc|admin|", actor=900, message_id=anchor)
    await tap(j, "a_root", actor=900)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_home_catalog_cities_empty_state_and_single_panel(journey, database, settings, lang):
    async with database() as s:
        await Service(settings, s).set_language(1, lang)
    await open_catalog(journey)
    anchor, _ = screen(journey)
    with use_language(lang):
        assert CATALOGS[lang]["mc.title"] in screen(journey)[1].text
    assert CATALOGS[lang]["mc.search"] not in str(screen(journey)[1].reply_markup)
    await tap(journey, "cities")
    for code in ("vilnius", "kaunas", "klaipeda", "siauliai", "panevezys", "pajurys"):
        await tap(journey, "list", value=code)
        assert screen(journey)[0] == anchor
        assert CATALOGS[lang]["mc.empty_city"] in screen(journey)[1].text
        await tap(journey, "cities")
    assert not any(isinstance(call, SendPhoto) for call in journey.transport.calls)
    assert sum(isinstance(call, SendMessage) for call in journey.transport.calls) == 1


async def test_admin_search_not_found_and_no_automatic_general_lookup(journey):
    await open_admin_catalog(journey)
    await tap(journey, "a_search", actor=900)
    await journey.send("Missing Company", actor=900)
    call = screen(journey)[1]
    assert "Įrašų kol kas nėra" in call.text
    assert "Missing Company" in call.text
    assert all(
        not (b.callback_data or "").startswith("sc|lookup")
        for row in call.reply_markup.inline_keyboard
        for b in row
    )
    assert await journey.state(900) is None


async def test_search_state_does_not_capture_ask_and_rep_commands(journey):
    for command in ("/ask 42", "/rep 42"):
        await open_admin_catalog(journey)
        await tap(journey, "a_search", actor=900)
        await journey.send(command, actor=900)
        assert "42" in screen(journey)[1].text
        assert await journey.state(900) != MerchantFlow.input.state


async def test_search_rate_limiter_survives_flow_data_clearing(journey):
    await open_admin_catalog(journey)
    await tap(journey, "a_search", actor=900)
    await journey.send("No Match", actor=900)
    await tap(journey, "a_search", actor=900)
    await journey.send("Other Match", actor=900)
    assert "Per daug užklausų" in screen(journey)[1].text


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_review_html_preserves_long_approved_comments_within_telegram_limit(lang):
    import re
    from html import unescape

    user = SimpleNamespace(username="seller", display_name="<unsafe>", telegram_id=42)
    entry = SimpleNamespace(owner=user)
    row = {"value": 1, "date": datetime.now(UTC), "comment": "😀" * 1500, "User": user}
    with use_language(lang):
        text = views.reviews(entry, {"score": 1, "positive": 1, "negative": 0}, [row], False)
    assert "😀" * 1500 in text
    assert "<unsafe>" not in text
    assert len(unescape(re.sub(r"<[^>]+>", "", text)).encode("utf-16-le")) // 2 <= 4096


async def test_real_profile_reviews_filters_and_check_activity_use_one_message(
    journey, database, settings
):
    from test_merchants_data import merchant

    async with database() as s:
        entry, user = await merchant(s, settings, 42, "<North>& Studio")
        for i, sign in enumerate((1, 1, -1)):
            author = await Service(settings, s).observe(
                100 + i, "reviewer" + str(i), "Private Reviewer " + str(i)
            )
            s.add(
                ReputationRequest(
                    reference="merchant-comment-" + str(i),
                    giver_user_id=author.id,
                    receiver_user_id=user.id,
                    value=sign,
                    request_key="merchant-comment-" + str(i),
                    status="APPROVED",
                    comment="Approved <review> number " + str(i),
                )
            )
        await s.commit()
    await open_catalog(journey)
    anchor, _ = screen(journey)
    await tap(journey, "cities")
    await tap(journey, "list", value="vilnius")
    await tap(journey, "profile")
    assert "&lt;North&gt;&amp; Studio" in screen(journey)[1].text
    assert "Bendra reputacija:</b> <b>+1" in screen(journey)[1].text
    await tap(journey, "reviews")
    assert "1 / 3" in str(screen(journey)[1].reply_markup)
    assert "Autorius neviešinamas" in screen(journey)[1].text
    assert "reviewer" not in screen(journey)[1].text
    await tap(journey, "reviews", value="neg")
    assert "👎 −REP" in screen(journey)[1].text
    assert "&lt;review&gt;" in screen(journey)[1].text
    await tap(journey, "profile")
    await tap(journey, "check")
    assert "42" in screen(journey)[1].text
    await tap(journey, "profile")
    await tap(journey, "activity")
    assert "42" in screen(journey)[1].text
    await tap(journey, "profile")
    await tap(journey, "list")
    assert screen(journey)[0] == anchor
    assert sum(isinstance(call, SendMessage) for call in journey.transport.calls) == 1


@pytest.mark.parametrize("name,query", [("Urban Studio", "URBAN"), ("ß" * 40, "ß" * 40)])
async def test_search_query_preserved_for_profiles_and_pagination(
    journey, database, settings, name, query
):
    from test_merchants_data import merchant

    async with database() as s:
        for i in range(8):
            await merchant(s, settings, 100 + i, name + " " + str(i))
    await open_admin_catalog(journey)
    await tap(journey, "a_search", actor=900)
    await journey.send(query, actor=900)
    assert "Registruoti prekeiviai: 8" in screen(journey)[1].text
    # Generic update budgets apply too, while the search limiter is reset only for test time.
    import app.bot.merchant_limits as limits

    original = limits.monotonic
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(limits, "monotonic", lambda: original() + 3)
        await tap(journey, actor=900, forged=Merchant(action="a_list", page=1).pack())
    assert "2 / 2" in str(screen(journey)[1].reply_markup)
    await tap(journey, "a_entry", actor=900)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(limits, "monotonic", lambda: original() + 6)
        await tap(journey, "a_root", actor=900)
        await tap(journey, "a_list", actor=900)
    assert "2 / 2" in str(screen(journey)[1].reply_markup)


async def test_expired_input_clears_session_and_cannot_run_a_query(journey):
    import time

    await open_admin_catalog(journey)
    await tap(journey, "a_search", actor=900)
    ctx = journey.dp.fsm.get_context(bot=journey.bot, chat_id=900, user_id=900)
    state = await ctx.get_data()
    data = state["mc_input"]
    data["until"] = time.time() - 1
    await ctx.update_data(mc_input=data)
    await journey.send("Valid Query", actor=900)
    assert "sesija baigėsi" in screen(journey)[1].text
    assert await journey.state(900) is None


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_old_registration_callbacks_are_rejected_without_creating_entries(
    journey, database, settings, lang
):
    async with database() as s:
        await Service(settings, s).set_language(1, lang)
    await open_catalog(journey)
    anchor, call = screen(journey)
    for action in ("terms", "begin", "info", "search"):
        await tap(journey, forged=Merchant(action=action).pack())
        result = journey.transport.calls[-1]
        assert isinstance(result, AnswerCallbackQuery) and result.show_alert
        error = "merchant_stale"
        assert result.text == CATALOGS[lang]["error." + error]
        assert screen(journey)[0] == anchor and screen(journey)[1].text == call.text
    async with database() as s:
        assert await s.scalar(select(MerchantEntry)) is None
        assert await s.scalar(select(MerchantProposal)) is None


async def test_public_city_pagination_drops_old_search_and_keeps_return_page(
    journey, database, settings
):
    from test_merchants_data import merchant

    async with database() as s:
        for i in range(8):
            await merchant(s, settings, 100 + i, "City Studio " + str(i))
    await open_catalog(journey)
    await tap(journey, "cities")
    await tap(journey, "list", value="vilnius")
    ctx = journey.dp.fsm.get_context(bot=journey.bot, chat_id=1, user_id=1)
    context = (await ctx.get_data())["mc_context"]
    context["query"] = "old unmatched search"
    await ctx.update_data(mc_context=context)
    await tap(journey, forged=Merchant(action="list", page=1).pack())
    assert "Registruoti prekeiviai: 8" in screen(journey)[1].text
    assert "2 / 2" in str(screen(journey)[1].reply_markup)
    assert "IEŠKOTI" not in str(screen(journey)[1].reply_markup)
    assert not (await ctx.get_data())["mc_context"]["query"]
    await tap(journey, "profile")
    await tap(journey, "list")
    assert "2 / 2" in str(screen(journey)[1].reply_markup)


async def test_old_public_search_input_is_closed_without_query(journey, monkeypatch):
    import time
    from unittest.mock import AsyncMock

    await open_catalog(journey)
    ctx = journey.dp.fsm.get_context(bot=journey.bot, chat_id=1, user_id=1)
    await ctx.set_state(MerchantFlow.input)
    await ctx.update_data(mc_input={"mode": "search", "admin": False, "until": time.time() + 60})
    listing = AsyncMock()
    monkeypatch.setattr(MerchantService, "listing", listing)
    await journey.send("Previous public search")
    assert "valdiklis paseno" in screen(journey)[1].text
    assert await journey.state() is None
    listing.assert_not_awaited()


async def test_storage_error_returns_a_localized_popup_without_private_details(
    journey, monkeypatch
):
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import SQLAlchemyError

    await open_catalog(journey)
    monkeypatch.setattr(
        MerchantService, "listing", AsyncMock(side_effect=SQLAlchemyError("PRIVATE_DSN"))
    )
    await tap(journey, "cities")
    before = screen(journey)[1].text
    await tap(journey, "list", value="vilnius")
    assert screen(journey)[1].text == before
    assert journey.transport.calls[-1].show_alert
    assert "PRIVATE_DSN" not in str(journey.transport.calls[-1])


async def test_redis_search_limit_uses_atomic_bot_specific_key():
    from unittest.mock import AsyncMock

    from app.bot.merchant_limits import SearchLimiter
    from app.errors import DomainError

    storage = SimpleNamespace(redis=SimpleNamespace(set=AsyncMock(return_value=True)))
    state = SimpleNamespace(storage=storage, key=SimpleNamespace(bot_id=7))
    limiter = SearchLimiter()
    await limiter.admit(state, 42)
    storage.redis.set.assert_awaited_once_with(
        "safecheck:merchants:search:7:42", "1", nx=True, ex=2
    )
    storage.redis.set.return_value = None
    with pytest.raises(DomainError, match="merchant_rate"):
        await limiter.admit(state, 42)


async def test_disabled_directory_has_a_working_exit_to_main_menu(journey, database, settings):
    async with database() as s:
        core = Service(settings, s)
        await core.set_language(900, "lt")
        await MerchantService(core).configure(900, 1, "enabled", False)
    await open_catalog(journey)
    assert "išjungtas" in screen(journey)[1].text
    buttons = [
        b.callback_data for row in screen(journey)[1].reply_markup.inline_keyboard for b in row
    ]
    assert "sc|home|" in buttons

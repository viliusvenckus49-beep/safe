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


async def registration(j, actor=42):
    await open_catalog(j, actor)
    await tap(j, "terms", actor=actor)
    await tap(j, "begin", actor=actor)
    await tap(j, "setcity", actor=actor, value="vilnius")
    await j.send("North Studio", actor=actor)
    await j.send("Lawful professional service description", actor=actor)
    await tap(j, "submit", actor=actor)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_home_catalog_cities_empty_state_and_single_panel(journey, database, settings, lang):
    async with database() as s:
        await Service(settings, s).set_language(1, lang)
    await open_catalog(journey)
    anchor, _ = screen(journey)
    with use_language(lang):
        assert CATALOGS[lang]["mc.title"] in screen(journey)[1].text
    await tap(journey, "cities")
    for code in ("vilnius", "kaunas", "klaipeda", "siauliai", "panevezys", "pajurys"):
        await tap(journey, "list", value=code)
        assert screen(journey)[0] == anchor
        assert CATALOGS[lang]["mc.empty_city"] in screen(journey)[1].text
        await tap(journey, "cities")
    assert not any(isinstance(call, SendPhoto) for call in journey.transport.calls)
    assert sum(isinstance(call, SendMessage) for call in journey.transport.calls) == 1


async def test_registration_guided_form_pending_and_admin_approval(journey, database, settings):
    await registration(journey)
    assert "LAUKIA PATVIRTINIMO" in screen(journey)[1].text
    async with database() as s:
        entry = await s.scalar(select(MerchantEntry))
        assert entry.status == "PENDING"
        pid = await s.scalar(select(MerchantProposal.id))
        assert (await MerchantService(Service(settings, s)).listing())[1] == 0
    await initial_menu(journey, 900)
    anchor, _ = screen(journey)
    await journey.click("sc|admin|", actor=900, message_id=anchor)
    await tap(journey, "a_root", actor=900)
    await tap(journey, "a_queue", actor=900)
    await tap(journey, "a_draft", actor=900)
    await tap(journey, "a_approve", actor=900)
    async with database() as s:
        assert (await s.get(MerchantProposal, pid)).status == "APPROVED"
        assert (await s.scalar(select(MerchantEntry))).name == "North Studio"


async def test_search_not_found_and_no_automatic_general_lookup(journey):
    await open_catalog(journey)
    await tap(journey, "search")
    await journey.send("Missing Company")
    call = screen(journey)[1]
    assert "PREKEIVIS NERASTAS" in call.text
    assert "Tai nereiškia" in call.text
    assert all(
        not (b.callback_data or "").startswith("sc|lookup")
        for row in call.reply_markup.inline_keyboard
        for b in row
    )
    assert await journey.state() is None


async def test_search_state_does_not_capture_ask_and_rep_commands(journey):
    for command in ("/ask 42", "/rep 42"):
        await open_catalog(journey)
        await tap(journey, "search")
        await journey.send(command)
        assert "42" in screen(journey)[1].text
        assert await journey.state() != MerchantFlow.input.state


async def test_private_proposal_and_admin_callbacks_cannot_be_forged(journey, database, settings):
    await registration(journey)
    async with database() as s:
        pid = await s.scalar(select(MerchantProposal.id))
    await open_catalog(journey, 43)
    before = screen(journey)[1].text
    await tap(journey, actor=43, forged=Merchant(action="draft", entry=pid).pack())
    assert (
        isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        and journey.transport.calls[-1].show_alert
    )
    assert screen(journey)[1].text == before
    await tap(journey, actor=43, forged=Merchant(action="a_settings").pack())
    assert journey.transport.calls[-1].show_alert
    assert screen(journey)[1].text == before


async def test_owner_can_cancel_pending_application(journey, database):
    await registration(journey)
    await tap(journey, "cancel", actor=42)
    async with database() as s:
        assert (await s.scalar(select(MerchantProposal))).status == "CANCELLED"
        assert (await s.scalar(select(MerchantEntry))).status == "REMOVED"


async def test_search_rate_limiter_survives_flow_data_clearing(journey):
    await open_catalog(journey)
    await tap(journey, "search")
    await journey.send("No Match")
    await tap(journey, "search")
    await journey.send("Other Match")
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


async def test_search_query_preserved_for_profiles_and_pagination(journey, database, settings):
    from test_merchants_data import merchant

    async with database() as s:
        for i in range(8):
            await merchant(s, settings, 100 + i, "Urban Studio " + str(i))
    await open_catalog(journey)
    await tap(journey, "search")
    await journey.send("URBAN")
    assert "Registruoti prekeiviai: 8" in screen(journey)[1].text
    # Generic update budgets apply too, while the search limiter is reset only for test time.
    import app.bot.merchant_limits as limits

    original = limits.monotonic
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(limits, "monotonic", lambda: original() + 3)
        await tap(journey, forged=Merchant(action="list", page=1).pack())
    assert "2 / 2" in str(screen(journey)[1].reply_markup)
    await tap(journey, "profile")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(limits, "monotonic", lambda: original() + 6)
        await tap(journey, "list")
    assert "2 / 2" in str(screen(journey)[1].reply_markup)


async def test_pending_rejection_reason_owner_edit_and_admin_settings(journey, database, settings):
    await registration(journey)
    await initial_menu(journey, 900)
    anchor, _ = screen(journey)
    await journey.click("sc|admin|", actor=900, message_id=anchor)
    await tap(journey, "a_root", actor=900)
    await tap(journey, "a_queue", actor=900)
    await tap(journey, "a_draft", actor=900)
    await tap(journey, "a_reject", actor=900)
    await journey.send("More details are required", actor=900)
    async with database() as s:
        proposal = await s.scalar(select(MerchantProposal))
        assert proposal.status == "REJECTED"
    await open_catalog(journey, 42)
    await tap(journey, "mine", actor=42)
    assert "More details are required" in screen(journey)[1].text
    assert "seller" not in screen(journey)[1].text  # No invented username.


async def test_expired_input_clears_session_and_cannot_run_a_query(journey):
    import time

    await open_catalog(journey)
    await tap(journey, "search")
    ctx = journey.dp.fsm.get_context(bot=journey.bot, chat_id=1, user_id=1)
    state = await ctx.get_data()
    data = state["mc_input"]
    data["until"] = time.time() - 1
    await ctx.update_data(mc_input=data)
    await journey.send("Valid Query")
    assert "laikas baigėsi" in screen(journey)[1].text
    assert await journey.state() is None


async def test_invalid_registration_input_can_be_corrected_without_an_extra_panel(journey):
    await open_catalog(journey, 42)
    anchor, _ = screen(journey)
    await tap(journey, "terms", actor=42)
    await tap(journey, "begin", actor=42)
    await tap(journey, "setcity", actor=42, value="kaunas")
    await journey.send("x", actor=42)
    assert "Netinkama įvestis" in screen(journey)[1].text
    await journey.send("Correct Studio", actor=42)
    await journey.send("Lawful business description", actor=42)
    assert "Correct Studio" in screen(journey)[1].text
    assert screen(journey)[0] == anchor


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

"""Navigation contracts exercised through real dispatcher routing and FSM."""

from types import SimpleNamespace

import pytest
from aiogram.methods import SendMessage, SendPhoto
from test_telegram import journey as telegram_journey

from app.bot import group_keyboards
from app.bot import keyboards as kb
from app.bot.callbacks import Action, ReputationModeration
from app.bot.states import InputFlow, ReportFlow
from app.i18n import t, use_language
from app.services import DomainError, Service

journey = telegram_journey


def last_message(j):
    item = next(
        item for item in reversed(j.transport.calls) if isinstance(item, (SendMessage, SendPhoto))
    )
    return (
        SimpleNamespace(text=item.caption, reply_markup=item.reply_markup)
        if isinstance(item, SendPhoto)
        else item
    )


def callbacks(markup):
    return (
        [button.callback_data for row in markup.inline_keyboard for button in row] if markup else []
    )


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
async def test_group_notifications_have_no_buttons(journey, database, settings, locale):
    async with database() as session:
        await Service(settings, session).set_language(900, locale)
    for command in [
        "+rep",
        "/report",
        "/scammers",
        "/add_trusted",
        "/add_trusted 42",
    ]:
        await journey.send(command, actor=900, chat=-100)
        assert last_message(journey).reply_markup is None, command


async def test_rep_decision_back_returns_to_queue_not_start(journey):
    await journey.send("+rep 42 Test comment")
    await journey.click(kb.action("rep_pending", "0"), actor=900)
    queue = callbacks(last_message(journey).reply_markup)
    reference = Action.unpack(queue[0]).value
    await journey.click(kb.action("rep_review", reference), actor=900)
    await journey.click(
        ReputationModeration(action="approve", reference=reference).pack(), actor=900
    )
    assert callbacks(last_message(journey).reply_markup) == [kb.action("rep_pending", "0")]
    await journey.click(kb.action("rep_pending", "0"), actor=900)
    assert kb.action("admin") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("admin"), actor=900)
    assert kb.action("rep_admin") in callbacks(last_message(journey).reply_markup)


@pytest.mark.parametrize(
    "route,value",
    [("users", "0"), ("audit", "0"), ("stats", ""), ("rep_admin", ""), ("admin_scams", "0")],
)
async def test_admin_sections_return_to_admin_panel(journey, route, value):
    await journey.click(kb.action(route, value), actor=900)
    values = callbacks(last_message(journey).reply_markup)
    assert kb.action("admin") in values
    assert kb.action("home") not in values
    assert kb.action("close") not in values
    await journey.click(kb.action("admin"), actor=900)
    assert kb.action("rep_admin") in callbacks(last_message(journey).reply_markup)


async def test_admin_registry_pagination_retains_admin_origin(journey, database, settings):
    async with database() as session:
        for target in range(40, 46):
            await Service(settings, session).add_scam(900, str(target))
    await journey.click(kb.action("admin_scams", "0"), actor=900)
    values = callbacks(last_message(journey).reply_markup)
    assert kb.action("admin_scams", "1") in values
    await journey.click(kb.action("admin_scams", "1"), actor=900)
    assert kb.action("admin") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("scams", "1"))
    assert kb.action("home") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("admin_scams", "0"))
    assert "⛔" in journey.text() or any(
        getattr(item, "show_alert", False) for item in journey.transport.calls
    )


async def test_scam_removal_back_keeps_operation_and_retargets(journey, database, settings):
    async with database() as session:
        await Service(settings, session).add_scam(900, "42")
    await journey.click(kb.action("del_sc"), actor=900)
    assert await journey.state(900) == InputFlow.admin_target.state
    assert kb.action("admin") in callbacks(last_message(journey).reply_markup)
    await journey.send("42", actor=900)
    await journey.send("tiny", actor=900)
    assert kb.action("admin_target_back") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("admin_target_back"), actor=900)
    assert await journey.state(900) == InputFlow.admin_target.state
    assert (await journey.data(900))["operation"] == "del_sc"
    await journey.send("42", actor=900)
    await journey.send("Removal reviewed by administrator", actor=900)
    assert callbacks(last_message(journey).reply_markup) == [kb.action("admin", "receipt")]
    assert await journey.state(900) is None


async def test_report_errors_keep_back_and_cancel_then_submit_has_no_cancel(journey):
    await journey.send("/report")
    await journey.send("42")
    await journey.send("tiny")
    assert await journey.state() == ReportFlow.reason.state
    values = callbacks(last_message(journey).reply_markup)
    assert any(value.startswith("draft:back:") for value in values)
    await journey.step("back")
    assert await journey.state() == ReportFlow.target.state
    await journey.send("43")
    await journey.send("Valid detailed report reason")
    await journey.step("preview")
    await journey.step("submit")
    assert callbacks(last_message(journey).reply_markup) == [kb.action("home")]
    assert await journey.state() is None


async def test_error_notifications_no_cancel_without_active_flow(journey, monkeypatch):
    await journey.send("/add_trusted 42")
    assert callbacks(last_message(journey).reply_markup) == [kb.action("home", "receipt")]

    async def unavailable(self, target, *, refresh_identity=False):
        raise DomainError("not_found")

    monkeypatch.setattr(Service, "profile", unavailable)
    await journey.send("/ask 42")
    assert callbacks(last_message(journey).reply_markup) == [kb.action("home")]
    await journey.send("/ask")
    await journey.send("42")
    assert kb.action("close") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("home"))
    assert await journey.state() is None


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
def test_static_screens_have_no_cancel_and_drafts_have_back(locale):
    with use_language(locale):
        static = [
            kb.administrator_help(),
            kb.administrator_help("reputation"),
            kb.administrator_menu([], 0, 0, 900),
            kb.administrator_input(completed=True),
            group_keyboards.group(-100, approved=False),
            group_keyboards.enrollment(-100, approved=True),
        ]
        for markup in static:
            assert kb.action("close") not in callbacks(markup)
        drafts = [
            kb.navigation(),
            kb.admin_rep_navigation("nonce", False),
            kb.report("nonce", "target"),
            group_keyboards.recovery_input(-100),
            group_keyboards.preview(-100, "nonce"),
        ]
        for markup in drafts:
            labels = [button.text for row in markup.inline_keyboard for button in row]
            assert t("button.back") in labels
            assert t("button.cancel") in labels


async def test_registry_search_returns_to_same_admin_page(journey, database, settings):
    async with database() as session:
        for target in range(40, 47):
            await Service(settings, session).add_scam(900, str(target))
    await journey.click(kb.action("lookup", "admin_scams:1"), actor=900)
    assert kb.action("admin_scams", "1") in callbacks(last_message(journey).reply_markup)
    await journey.send("bad target", actor=900)
    assert kb.action("admin_scams", "1") in callbacks(last_message(journey).reply_markup)
    await journey.send("42", actor=900)
    assert kb.action("admin_scams", "1") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("admin_scams", "1"), actor=900)
    assert kb.action("admin_scams", "0") in callbacks(last_message(journey).reply_markup)
    assert await journey.state(900) is None
    await journey.click(kb.action("lookup", "admin_scams:1"))
    assert await journey.state() is None


async def test_rep_decision_retains_queue_page_and_clamps_when_empty(journey, database, settings):
    async with database() as session:
        for actor in range(1, 8):
            await Service(settings, session).vote(
                actor, str(50 + actor), 1, -100, f"queue-page:{actor}", comment="Test comment"
            )
    await journey.click(kb.action("rep_pending", "1"), actor=900)
    reference = Action.unpack(callbacks(last_message(journey).reply_markup)[0]).value
    await journey.click(kb.action("rep_review", reference), actor=900)
    assert kb.action("rep_pending", "1") in callbacks(last_message(journey).reply_markup)
    await journey.click(
        ReputationModeration(action="reject", reference=reference).pack(), actor=900
    )
    assert callbacks(last_message(journey).reply_markup) == [kb.action("rep_pending", "1")]
    await journey.click(kb.action("rep_pending", "1"), actor=900)
    assert (await journey.data(900))["rep_queue_page"] == 1


async def test_admin_rep_wizard_back_and_completion(journey):
    from app.bot.callbacks import AdminRepStep
    from app.bot.states import AdminRepFlow

    await journey.click(kb.action("rep_add"), actor=900)
    assert kb.action("rep_admin") in callbacks(last_message(journey).reply_markup)
    await journey.click(kb.action("rep_admin"), actor=900)
    assert await journey.state(900) is None
    await journey.click(kb.action("rep_add"), actor=900)
    await journey.send("42", actor=900)
    await journey.send("2", actor=900)
    await journey.send("Reviewed and approved contribution", actor=900)
    nonce = (await journey.data(900))["nonce"]
    await journey.click(AdminRepStep(action="back", nonce=nonce).pack(), actor=900)
    assert await journey.state(900) == AdminRepFlow.reason.state
    await journey.send("Reviewed and approved contribution", actor=900)
    nonce = (await journey.data(900))["nonce"]
    await journey.click(AdminRepStep(action="submit", nonce=nonce).pack(), actor=900)
    assert callbacks(last_message(journey).reply_markup) == [kb.action("rep_admin")]
    assert await journey.state(900) is None


async def test_report_decision_returns_to_pending_reports(journey, database, settings):
    from app.bot.callbacks import Moderation

    async with database() as session:
        report = await Service(settings, session).submit_report(
            1, "42", "Detailed evidence and allegation", [], "navigation-report"
        )
    await journey.click(Moderation(action="reject", reference=report.reference).pack(), actor=900)
    assert callbacks(last_message(journey).reply_markup) == [kb.action("pending", "receipt")]
    await journey.click(kb.action("pending"), actor=900)
    assert kb.action("rep_admin") in callbacks(last_message(journey).reply_markup)


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
@pytest.mark.parametrize("chat", [None, -100])
async def test_result_buttons_include_cancel_in_private_and_group(
    journey, database, settings, locale, chat
):
    async with database() as session:
        await Service(settings, session).set_language(1, locale)
    for command in ["/ask 42", "/ask@redsafecheckbot 42", "/rep 42", "/profile"]:
        await journey.send(command, chat=chat)
        markup = last_message(journey).reply_markup
        names = [Action.unpack(value).name for value in callbacks(markup)]
        assert names == ["lookup", "vote+", "vote-", "home", "close"]
        with use_language(locale):
            assert markup.inline_keyboard[-1][0].text == t("button.cancel")
        before = len(journey.transport.deletions)
        await journey.click(kb.action("close"), chat=chat)
        assert len(journey.transport.deletions) == before + 1
        state = journey.dp.fsm.get_context(bot=journey.bot, chat_id=chat or 1, user_id=1)
        assert await state.get_state() is None

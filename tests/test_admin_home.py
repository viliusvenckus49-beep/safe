"""Private administration entry uses persisted roles and existing guarded routes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import AnswerCallbackQuery, SendDocument, SendMessage, SendPhoto
from test_telegram import journey as telegram_journey

from app.admin_services import AdminService
from app.bot import group_keyboards
from app.bot import keyboards as kb
from app.bot.callbacks import AdminAccess, AdminHelp, Language
from app.bot.group_keyboards import GroupAction
from app.bot.handlers import ADMIN_ACTIONS
from app.bot.screens import send_screen
from app.group_services import GroupService
from app.i18n import t, use_language
from app.services import Service

journey = telegram_journey
OWNER = 900
MODERATOR = 42


def values(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row] if markup else []


def last_screen(journey):
    return next(
        item
        for item in reversed(journey.transport.calls)
        if isinstance(item, (SendMessage, SendPhoto, SendDocument))
    )


async def prepare(database, settings, actor, lang):
    async with database() as session:
        service = Service(settings, session)
        await service.set_language(actor, lang)
        if actor == MODERATOR:
            await service.access.change(OWNER, str(actor), True, "a" * 32)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("actor", [1, MODERATOR, OWNER])
async def test_private_start_exposes_only_existing_role_permissions(
    journey, database, settings, lang, actor
):
    await prepare(database, settings, actor, lang)
    await journey.send("/start", actor=actor)
    screen = last_screen(journey)
    assert isinstance(screen, SendPhoto)
    with use_language(lang):
        assert screen.reply_markup == kb.home(actor != 1, owner=actor == OWNER)
        if actor != 1:
            labels = [b.text for row in screen.reply_markup.inline_keyboard for b in row]
            assert t("button.admin") in labels
    routes = values(screen.reply_markup)
    assert set(routes) - set(values(kb.home())) == ({kb.action("admin")} if actor != 1 else set())
    assert not (set(routes) & (set(values(kb.admin(True))) - {kb.action("home")}))
    assert (kb.action("admin") in routes) is (actor != 1)
    if actor == 1:
        return
    await journey.click(kb.action("admin"), actor=actor)
    with use_language(lang):
        assert last_screen(journey).reply_markup == kb.admin(actor == OWNER)
    routes = values(last_screen(journey).reply_markup)
    assert (GroupAction(action="list").pack() in routes) is (actor == OWNER)
    assert (AdminAccess(action="list").pack() in routes) is (actor == OWNER)


async def test_trusted_status_does_not_grant_management_menu(journey, database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.set_language(1, "lt")
        await service.set_trusted(OWNER, "1", True, "trusted-home")
    await journey.send("/start")
    assert last_screen(journey).reply_markup == kb.home()


@pytest.mark.parametrize("actor", [MODERATOR, OWNER])
async def test_first_language_selection_and_home_recheck_callback_actor(
    journey, database, settings, actor
):
    if actor == MODERATOR:
        async with database() as session:
            await AdminService(settings, session).change(OWNER, str(actor), True, "a" * 32)
    await journey.send("/start", actor=actor)
    assert kb.action("admin") not in values(last_screen(journey).reply_markup)
    for lang in ("lt", "en", "ru"):
        await journey.click(Language(lang=lang).pack(), actor=actor)
        with use_language(lang):
            assert last_screen(journey).reply_markup == kb.home(True, owner=actor == OWNER)
        await journey.click(kb.action("home"), actor=actor)
        with use_language(lang):
            assert last_screen(journey).reply_markup == kb.home(True, owner=actor == OWNER)


@pytest.mark.parametrize("actor", [1, MODERATOR, OWNER])
async def test_group_start_never_exposes_global_management(
    journey, database, settings, monkeypatch, actor
):
    await prepare(database, settings, actor, "lt")
    monkeypatch.setattr(
        journey.bot,
        "get_chat_member",
        AsyncMock(return_value=SimpleNamespace(status="administrator", can_restrict_members=True)),
    )
    await journey.send("/start", actor=actor, chat=-100)
    assert last_screen(journey).reply_markup == kb.home(private=False)
    await journey.click(kb.action("home"), actor=actor, chat=-100)
    assert last_screen(journey).reply_markup == kb.home(private=False)
    for name in ("admin", "status"):
        await journey.click(kb.action(name), actor=actor, chat=-100)
        assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        assert journey.transport.calls[-1].show_alert
        assert last_screen(journey).reply_markup == kb.home(private=False)


def test_owner_flag_alone_or_group_context_cannot_expose_management():
    assert kb.home(owner=True) == kb.home()
    assert kb.home(True, owner=True, private=False) == kb.home(private=False)


@pytest.mark.parametrize("return_route", [kb.action("home"), Language(lang="en").pack()])
@pytest.mark.parametrize("protected_route", [kb.action("add_sc"), kb.action("status")])
async def test_revocation_hides_buttons_and_rejects_saved_callback(
    journey, database, settings, return_route, protected_route
):
    await prepare(database, settings, MODERATOR, "lt")
    await journey.send("/start", actor=MODERATOR)
    assert kb.action("admin") in values(last_screen(journey).reply_markup)
    await journey.click(kb.action("admin"), actor=MODERATOR)
    assert kb.action("add_sc") in values(last_screen(journey).reply_markup)
    async with database() as session:
        await AdminService(settings, session).change(OWNER, str(MODERATOR), False, "b" * 32)
    await journey.click(protected_route, actor=MODERATOR)
    assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
    assert journey.transport.calls[-1].show_alert
    assert await journey.state(MODERATOR) is None
    await journey.click(return_route, actor=MODERATOR)
    with use_language("en" if return_route.startswith("language:") else "lt"):
        assert last_screen(journey).reply_markup == kb.home()


@pytest.mark.parametrize("name", sorted(ADMIN_ACTIONS))
async def test_ordinary_user_cannot_execute_forged_management_shortcut(journey, name):
    await journey.click(kb.action(name, "42"))
    assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
    assert journey.transport.calls[-1].show_alert
    assert await journey.state() is None
    assert not any(isinstance(item, (SendMessage, SendPhoto)) for item in journey.transport.calls)


@pytest.mark.parametrize(
    "route",
    [AdminHelp().pack(), AdminAccess(action="list").pack(), GroupAction(action="list").pack()],
)
async def test_ordinary_user_cannot_execute_other_protected_callback_families(journey, route):
    await journey.click(route)
    assert values(last_screen(journey).reply_markup) == [kb.action("home", "receipt")]
    assert await journey.state() is None


@pytest.mark.parametrize(
    "route", [AdminAccess(action="list").pack(), GroupAction(action="list").pack()]
)
async def test_moderator_cannot_execute_owner_only_menu_routes(journey, database, settings, route):
    await prepare(database, settings, MODERATOR, "lt")
    await journey.click(route, actor=MODERATOR)
    assert values(last_screen(journey).reply_markup) == [kb.action("home", "receipt")]
    assert await journey.state(MODERATOR) is None


@pytest.mark.parametrize(
    "route",
    [
        kb.action("admin"),
        kb.action("trusted_admin"),
        kb.action("pending"),
        kb.action("rep_pending", "0"),
        kb.action("rep_admin"),
        kb.action("admin_scams", "0"),
        kb.action("add_sc"),
        kb.action("del_sc"),
        kb.action("users", "0"),
        kb.action("stats"),
        kb.action("audit"),
        kb.action("status"),
        AdminHelp().pack(),
    ],
)
async def test_moderator_administration_opens_existing_screens_with_back(
    journey, database, settings, route
):
    await prepare(database, settings, MODERATOR, "lt")
    await journey.send("/start", actor=MODERATOR)
    assert kb.action("admin") in values(last_screen(journey).reply_markup)
    await journey.click(kb.action("admin"), actor=MODERATOR)
    assert route in values(last_screen(journey).reply_markup) or route == kb.action("admin")
    await journey.click(route, actor=MODERATOR)
    routes = values(last_screen(journey).reply_markup)
    assert kb.action("admin") in routes or kb.action("home") in routes
    await journey.click(kb.action("home"), actor=MODERATOR)
    assert kb.action("admin") in values(last_screen(journey).reply_markup)
    assert kb.action("add_sc") not in values(last_screen(journey).reply_markup)
    assert await journey.state(MODERATOR) is None


@pytest.mark.parametrize("command", ["/profile", "/help", "/ask 43", "+rep"])
async def test_private_result_back_returns_to_authorized_home(journey, database, settings, command):
    await prepare(database, settings, MODERATOR, "lt")
    await journey.send(command, actor=MODERATOR)
    back = kb.action("home", "receipt" if command.startswith("/ask") else "")
    assert back in values(last_screen(journey).reply_markup)
    await journey.click(back, actor=MODERATOR)
    assert kb.action("admin") in values(last_screen(journey).reply_markup)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("actor", [MODERATOR, OWNER])
async def test_status_button_matches_command_and_returns_to_administration(
    journey, database, settings, monkeypatch, lang, actor
):
    await prepare(database, settings, actor, lang)
    monkeypatch.setattr(
        "app.health.snapshot", lambda: {"healthy": True, "uptime": 120, "poll": 2, "worker": 3}
    )
    await journey.send("/start", actor=actor)
    assert kb.action("status") not in values(last_screen(journey).reply_markup)
    await journey.click(kb.action("admin"), actor=actor)
    menu = last_screen(journey).reply_markup
    buttons = {b.callback_data: b.text for row in menu.inline_keyboard for b in row}
    with use_language(lang):
        assert buttons[kb.action("status")] == t("button.status")
    await journey.send("/status", actor=actor)
    expected = last_screen(journey).text
    await journey.click(kb.action("status"), actor=actor)
    status = last_screen(journey)
    assert status.text == expected
    assert values(status.reply_markup) == [kb.action("admin")]
    await journey.click(kb.action("admin"), actor=actor)
    assert kb.action("status") in values(last_screen(journey).reply_markup)


async def test_group_export_has_back_to_group_and_owner_guard(journey, database, settings):
    async with database() as session:
        await GroupService(settings, session).register_group(OWNER, -100, "Test group", True)
    route = GroupAction(action="export", chat_id=-100).pack()
    await journey.click(route, actor=OWNER)
    document = last_screen(journey)
    assert isinstance(document, SendDocument)
    assert document.reply_markup == group_keyboards.back_to_group(-100)
    await journey.click(values(document.reply_markup)[0], actor=OWNER)
    assert GroupAction(action="list").pack() in values(last_screen(journey).reply_markup)
    await journey.click(route)
    assert values(last_screen(journey).reply_markup) == [kb.action("home", "receipt")]


@pytest.mark.parametrize("private", [True, False])
async def test_default_back_does_not_override_existing_navigation(private):
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private" if private else "supergroup"), answer=AsyncMock()
    )
    await send_screen(message, "Notice", None, False)
    assert values(message.answer.await_args.kwargs["reply_markup"]) == (
        [kb.action("home")] if private else []
    )
    markup = kb.navigation("admin")
    await send_screen(message, "Draft", markup, False)
    assert message.answer.await_args.kwargs["reply_markup"] is markup

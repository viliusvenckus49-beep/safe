"""Promoted icons retain group/top routes and rejected icons cannot break navigation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText, SendMessage, SendPhoto

from app.bot import group_keyboards, keyboards
from app.bot.session import _request
from app.i18n import use_button_icons, use_language
from app.locales.button_icons import CATALOGS as ICONS


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_all_locales_share_icons_and_keep_dynamic_names_unchanged(lang):
    with use_language(lang):
        buttons = {b.callback_data: b for row in keyboards.home(True).inline_keyboard for b in row}
        assert buttons["sc|lookup|"].icon_custom_emoji_id is None
        assert buttons["sc|profile|"].icon_custom_emoji_id is None
        assert buttons["sc|admin|"].icon_custom_emoji_id is None
        assert ICONS[lang] == ICONS["lt"]
        assert ICONS[lang] == {}
        button = group_keyboards.menu(
            [SimpleNamespace(title="Original Group", chat_id=-100)], admin=False
        ).inline_keyboard[0][0]
        assert button.text == "Original Group" and button.icon_custom_emoji_id is None
        assert button.callback_data == "sg|group|-100|"


@pytest.mark.parametrize("method_type", [SendMessage, SendPhoto, EditMessageText])
async def test_rejected_native_icons_retry_once_preserving_all_content_and_callbacks(method_type):
    with use_button_icons({"lt": {"button.lookup": "5357123175136118943"}}):
        markup = keyboards.home(True)
    values = {"chat_id": 900, "reply_markup": markup}
    if method_type is SendPhoto:
        values.update(photo="file-id", caption="Same caption", parse_mode="HTML")
    else:
        values.update(text="Same text", parse_mode="HTML")
    if method_type is EditMessageText:
        values["message_id"] = 42
    method = method_type(**values)
    original = method.model_dump()
    result = object()
    request = AsyncMock(side_effect=[TelegramBadRequest(method, "BUTTON_ICON_INVALID"), result])
    assert await _request(request, None, method, icon_fallback=True) is result
    assert request.await_count == 2
    retry = request.await_args_list[1].args[1]
    assert retry.model_dump(exclude={"reply_markup"}) == method.model_dump(exclude={"reply_markup"})
    assert [[b.callback_data for b in row] for row in retry.reply_markup.inline_keyboard] == [
        [b.callback_data for b in row] for row in markup.inline_keyboard
    ]
    assert [[b.text for b in row] for row in retry.reply_markup.inline_keyboard] == [
        [b.text for b in row] for row in markup.inline_keyboard
    ]
    assert all(
        b.icon_custom_emoji_id is None for row in retry.reply_markup.inline_keyboard for b in row
    )
    assert method.model_dump() == original
    assert ICONS["lt"] == {}


@pytest.mark.parametrize("icon_fallback,lang", [(False, "lt"), (True, "en")])
async def test_studio_or_non_icon_errors_do_not_retry(icon_fallback, lang):
    with use_language(lang), use_button_icons({}):
        method = SendMessage(chat_id=900, text="Same", reply_markup=keyboards.home())
    request = AsyncMock(side_effect=TelegramBadRequest(method, "OTHER_API_ERROR"))
    with pytest.raises(TelegramBadRequest, match="OTHER_API_ERROR"):
        await _request(request, None, method, icon_fallback=icon_fallback)
    assert request.await_count == 1


async def test_failed_fallback_is_reported_as_failure_and_never_retried_again():
    with use_button_icons({"lt": {"button.lookup": "5357123175136118943"}}):
        method = SendMessage(chat_id=900, text="Same", reply_markup=keyboards.home())
    request = AsyncMock(
        side_effect=[
            TelegramBadRequest(method, "BUTTON_ICON_INVALID"),
            TelegramBadRequest(method, "CHAT_NOT_FOUND"),
        ]
    )
    with pytest.raises(TelegramBadRequest, match="CHAT_NOT_FOUND"):
        await _request(request, None, method, icon_fallback=True)
    assert request.await_count == 2


async def test_network_failure_does_not_repeat_a_possibly_delivered_message():
    with use_button_icons({"lt": {"button.lookup": "5357123175136118943"}}):
        method = SendMessage(chat_id=900, text="Same", reply_markup=keyboards.home())
    request = AsyncMock(side_effect=TelegramNetworkError(method, "Connection lost"))
    with pytest.raises(TelegramNetworkError):
        await _request(request, None, method, icon_fallback=True)
    assert request.await_count == 1


def test_main_admin_and_result_routes_are_identical_in_every_language():
    routes = []
    for lang in ("lt", "en", "ru"):
        with use_language(lang):
            routes.append(
                [
                    [[(b.callback_data, b.url) for b in row] for row in markup.inline_keyboard]
                    for markup in (
                        keyboards.home(True, owner=True),
                        keyboards.admin(owner=True),
                        keyboards.result("u:8425927753"),
                        group_keyboards.group(-100),
                    )
                ]
            )
    assert routes[0] == routes[1] == routes[2]

"""Promoted icons retain group/top routes and rejected icons cannot break navigation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import EditMessageText, SendMessage, SendPhoto

from app.bot import group_keyboards, keyboards
from app.bot.scam_admin import listing
from app.bot.session import _request
from app.i18n import t, use_language
from app.locales.button_icons import CATALOGS as ICONS


def test_promoted_icons_reach_recovery_and_top_without_changing_dynamic_names():
    with use_language("lt"):
        for markup in (
            group_keyboards.menu([], admin=True),
            group_keyboards.consent(-100),
            group_keyboards.preview(-100, "nonce"),
            group_keyboards.recovery_input(-100),
        ):
            buttons = [b for row in markup.inline_keyboard for b in row]
            back = next(b for b in buttons if b.text == t("button.back"))
            assert back.icon_custom_emoji_id == "5321143340744329564"
            for button in buttons:
                if button.text == t("button.cancel"):
                    assert button.icon_custom_emoji_id == "5226886710020820160"
                    assert button.callback_data == "sc|close|"
        top = keyboards.leaderboard(
            [
                {
                    "user": SimpleNamespace(
                        username="Original_Name", telegram_id=42, display_name="Name"
                    )
                }
            ]
        ).inline_keyboard
        assert top[0][0].text == "1. Name"
        assert top[0][0].url == "https://t.me/Original_Name"
        assert top[-1][0].icon_custom_emoji_id == "5332477471675661125"
        assert top[-1][0].callback_data == "sc|home|"


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_other_locales_and_dynamic_button_names_do_not_inherit_lithuanian_icons(lang):
    with use_language(lang):
        assert all(
            b.icon_custom_emoji_id is None
            for row in keyboards.home(True).inline_keyboard
            for b in row
        )
    with use_language("lt"):
        button = group_keyboards.menu(
            [SimpleNamespace(title="Original Group", chat_id=-100)], admin=False
        ).inline_keyboard[0][0]
        assert button.text == "Original Group" and button.icon_custom_emoji_id is None
        assert button.callback_data == "sg|consent|-100|"


@pytest.mark.parametrize("method_type", [SendMessage, SendPhoto, EditMessageText])
async def test_rejected_native_icons_retry_once_preserving_all_content_and_callbacks(method_type):
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
    assert ICONS["lt"]["button.lookup"] == "5357123175136118943"


@pytest.mark.parametrize("icon_fallback,lang", [(False, "lt"), (True, "en")])
async def test_studio_or_non_icon_errors_do_not_retry(icon_fallback, lang):
    with use_language(lang):
        method = SendMessage(chat_id=900, text="Same", reply_markup=keyboards.home())
    request = AsyncMock(side_effect=TelegramBadRequest(method, "OTHER_API_ERROR"))
    with pytest.raises(TelegramBadRequest, match="OTHER_API_ERROR"):
        await _request(request, None, method, icon_fallback=icon_fallback)
    assert request.await_count == 1


async def test_failed_fallback_is_reported_as_failure_and_never_retried_again():
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
    method = SendMessage(chat_id=900, text="Same", reply_markup=keyboards.home())
    request = AsyncMock(side_effect=TelegramNetworkError(method, "Connection lost"))
    with pytest.raises(TelegramNetworkError):
        await _request(request, None, method, icon_fallback=True)
    assert request.await_count == 1


async def test_scam_registry_keeps_native_back_icon_when_combining_pagination(monkeypatch):
    render = AsyncMock()
    monkeypatch.setattr("app.bot.scam_admin.flow_screen", render)
    service = SimpleNamespace(require_admin=AsyncMock(), scams=AsyncMock(return_value=([], 0)))
    state = AsyncMock()
    state.get_data.return_value = {}
    with use_language("lt"):
        await listing(SimpleNamespace(chat=SimpleNamespace(id=900)), state, service)
    service.require_admin.assert_awaited_once_with(900)
    markup = render.await_args.kwargs["reply_markup"]
    buttons = [b for row in markup.inline_keyboard for b in row]
    back = next(b for b in buttons if b.callback_data == "sc|admin|")
    assert back.text == "ᴀᴛɢᴀʟ" and back.icon_custom_emoji_id == "5321143340744329564"
    assert [b.callback_data for b in buttons[:3]] == [
        "sc|admin_scams|0",
        "sc|noop|",
        "sc|admin_scams|0",
    ]

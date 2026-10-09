"""Native Telegram custom icons and the real result templates remain isolated previews."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetCustomEmojiStickers, SendMessage, SendPhoto
from aiogram.types import MessageEntity, Sticker
from test_ui_studio import studio as studio
from test_ui_studio import value

from app.bot import keyboards as kb
from app.bot.callbacks import Action
from app.i18n import CATALOGS, t
from app.locales.button_icons import CATALOGS as SHIPPED_ICONS
from app.test_ui.__main__ import only_ui_requests
from app.test_ui.preview import screen
from app.test_ui.profile import RESULT_KEYS, Design, identifiers
from app.test_ui.router import Input, cb
from app.test_ui.texts import text

ICON = "5368324170671202286"
OTHER_ICON = "5368324170671202287"


@pytest.fixture
def lookup(studio, monkeypatch):
    sticker = Sticker(
        file_id="test-file",
        file_unique_id="test-unique",
        type="custom_emoji",
        width=100,
        height=100,
        is_animated=True,
        is_video=False,
        custom_emoji_id=ICON,
    )
    request = AsyncMock(return_value=[sticker])
    monkeypatch.setattr(studio.bot, "get_custom_emoji_stickers", request)
    return request


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_real_premium_entity_becomes_native_button_icon_and_survives_restart(
    studio, lookup, lang
):
    await studio.click(cb("lang", lang), actor=900)
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    assert await studio.state(900) == Input.icon.state
    await studio.send(
        "📑",
        actor=900,
        entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id=ICON)],
    )
    lookup.assert_awaited_once_with(custom_emoji_ids=[ICON])
    assert studio.design.icon(lang, "button.lookup") == ICON
    assert await studio.state(900) is None
    await studio.click(cb("preview", "home_admin"), actor=900)
    photo = studio.transport.calls[-1]
    assert isinstance(photo, SendPhoto)
    button = next(
        b
        for row in photo.reply_markup.inline_keyboard
        for b in row
        if b.callback_data == Action(name="lookup").pack()
    )
    assert button.icon_custom_emoji_id == ICON
    assert button.text == studio.design.text(lang, "button.lookup")
    assert button.model_dump(exclude_none=True)["icon_custom_emoji_id"] == ICON
    assert Design(studio.design.path).icon(lang, "button.lookup") == ICON
    await studio.click(cb("remove_icon", value("button.lookup")), actor=900)
    assert studio.design.icon(lang, "button.lookup") is None
    assert studio.design.text(lang, "button.lookup") == CATALOGS[lang]["button.lookup"]


async def test_numeric_emoji_id_is_verified_and_exported_without_permissions(studio, lookup):
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    await studio.send(ICON, actor=900)
    await studio.click(cb("sample", value("button.lookup")), actor=900)
    assert (
        studio.transport.calls[-1].reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == ICON
    )
    await studio.click(cb("export"), actor=900)
    data = json.loads(studio.transport.calls[-1].document.data)
    assert data["icons"]["lt"]["button.lookup"] == ICON
    assert "admins" not in data and "bot_token" not in data


@pytest.mark.parametrize("candidate", ["📑", "0", "-1", "@emoji", "９９", "18446744073709551616"])
async def test_unicode_and_invalid_ids_do_not_turn_into_custom_icons(studio, lookup, candidate):
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    await studio.send(candidate, actor=900)
    assert studio.design.icon("lt", "button.lookup") is None
    assert await studio.state(900) == Input.icon.state
    lookup.assert_not_awaited()


async def test_multiple_custom_emoji_require_one_selection(studio, lookup):
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    await studio.send(
        "📑📑",
        actor=900,
        entities=[
            MessageEntity(type="custom_emoji", offset=index, length=2, custom_emoji_id=ICON)
            for index in (0, 2)
        ],
    )
    lookup.assert_not_awaited()
    assert studio.design.icon("lt", "button.lookup") is None


async def test_unknown_or_failed_telegram_emoji_lookup_does_not_save(studio, lookup):
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    lookup.return_value = []
    await studio.send(ICON, actor=900)
    assert studio.design.icon("lt", "button.lookup") is None
    lookup.side_effect = TelegramBadRequest(
        GetCustomEmojiStickers(custom_emoji_ids=[ICON]), "STICKER_INVALID"
    )
    await studio.send(ICON, actor=900)
    assert studio.design.icon("lt", "button.lookup") is None
    assert await studio.state(900) == Input.icon.state


async def test_concurrent_icon_edit_does_not_overwrite_a_new_selection(studio, lookup):
    await studio.click(cb("icon", value("button.lookup")), actor=900)
    await studio.click(cb("icon", value("button.lookup")), actor=901)
    await studio.send(ICON, actor=901)
    await studio.send(OTHER_ICON, actor=900)
    assert studio.design.icon("lt", "button.lookup") == ICON
    assert lookup.await_count == 1
    assert await studio.state(900) is None


async def test_permission_and_group_guards_apply_before_emoji_api_calls(studio, lookup):
    await studio.click(cb("icon", value("button.lookup")), actor=1)
    await studio.send(ICON, actor=1)
    await studio.click(cb("icon", value("button.lookup")), actor=900, chat=-1000)
    await studio.send(ICON, actor=900, chat=-1000)
    lookup.assert_not_awaited()
    assert studio.design.data["icons"] == {}


async def test_transport_allows_read_only_custom_emoji_lookup():
    request = AsyncMock()
    method = GetCustomEmojiStickers(custom_emoji_ids=[ICON])
    await only_ui_requests(request, None, method)
    request.assert_awaited_once_with(None, method)


def test_identical_button_labels_keep_distinct_icons_and_callbacks(tmp_path):
    design = Design(tmp_path / "design.json")
    for key, icon in (("button.profile", ICON), ("button.rep", OTHER_ICON)):
        design.set_text("lt", key, "SAME LABEL")
        design.set_icon("lt", key, icon)
    _, markup = screen("home_admin", design, "lt")
    buttons = {b.callback_data: b for row in markup.inline_keyboard for b in row}
    assert buttons[Action(name="profile").pack()].icon_custom_emoji_id == ICON
    assert buttons[Action(name="rep").pack()].icon_custom_emoji_id == OTHER_ICON
    assert (
        kb.home().inline_keyboard[1][0].icon_custom_emoji_id
        == SHIPPED_ICONS["lt"]["button.profile"]
    )
    assert t("button.profile", "lt") == CATALOGS["lt"]["button.profile"]
    assert t("button.profile", "lt").icon_custom_emoji_id == SHIPPED_ICONS["lt"]["button.profile"]


async def test_icons_do_not_leak_out_of_concurrent_preview_contexts(tmp_path):
    design = Design(tmp_path / "design.json")
    design.set_icon("lt", "button.lookup", ICON)
    entered, release = asyncio.Event(), asyncio.Event()

    async def preview():
        with design.preview("lt"):
            entered.set()
            await release.wait()
            return kb.home().inline_keyboard[0][0].icon_custom_emoji_id

    task = asyncio.create_task(preview())
    await entered.wait()
    assert (
        kb.home().inline_keyboard[0][0].icon_custom_emoji_id == SHIPPED_ICONS["lt"]["button.lookup"]
    )
    release.set()
    assert await task == ICON


def test_existing_design_is_loaded_without_resetting_text_or_layout(tmp_path):
    path = tmp_path / "design.json"
    legacy = {
        "version": 1,
        "texts": {"lt": {"button.lookup": "KEEP"}},
        "layouts": {"home": identifiers("home")[::-1]},
    }
    path.write_text(json.dumps(legacy))
    design = Design(path)
    design.set_icon("lt", "button.lookup", ICON)
    saved = json.loads(path.read_text())
    assert saved["texts"] == legacy["texts"] and saved["layouts"] == legacy["layouts"]
    assert saved["version"] == 1 and saved["icons"]["lt"]["button.lookup"] == ICON


@pytest.mark.parametrize(
    "icons",
    [
        {"lt": {"p.warning": ICON}},
        {"de": {"button.lookup": ICON}},
        {"lt": {"button.lookup": "📑"}},
        {"lt": []},
        [],
    ],
)
def test_invalid_saved_icons_fail_closed_without_overwriting(tmp_path, icons):
    path = tmp_path / "design.json"
    original = json.dumps({"version": 1, "texts": {}, "layouts": {}, "icons": icons})
    path.write_text(original)
    with pytest.raises(ValueError):
        Design(path)
    assert path.read_text() == original


def test_failed_icon_persistence_keeps_previous_design(tmp_path, monkeypatch):
    design = Design(tmp_path / "design.json")
    design.set_icon("lt", "button.lookup", ICON)
    previous = design.export()

    def fail(*args):
        raise OSError("No space")

    monkeypatch.setattr("app.test_ui.profile.os.replace", fail)
    with pytest.raises(OSError):
        design.set_icon("lt", "button.lookup", OTHER_ICON)
    assert design.export() == previous == design.path.read_bytes()


async def test_telegram_icon_rejection_keeps_preview_accessible_and_choice_saved(
    studio, monkeypatch
):
    studio.design.set_icon("lt", "button.lookup", ICON)
    original = studio.transport.make_request
    rejected = []

    async def send(bot, method, timeout=None):
        if isinstance(method, SendPhoto) and any(
            b.icon_custom_emoji_id for row in method.reply_markup.inline_keyboard for b in row
        ):
            rejected.append(method)
            raise TelegramBadRequest(method, "BUTTON_ICON_INVALID")
        return await original(bot, method, timeout=timeout)

    monkeypatch.setattr(studio.transport, "make_request", send)
    await studio.click(cb("preview", "home_admin"), actor=900)
    assert len(rejected) == 1
    photo = next(c for c in reversed(studio.transport.calls) if isinstance(c, SendPhoto))
    assert all(
        b.icon_custom_emoji_id is None for row in photo.reply_markup.inline_keyboard for b in row
    )
    assert photo.caption == rejected[0].caption
    assert studio.design.icon("lt", "button.lookup") == ICON
    assert "Telegram Premium" in studio.text()


async def test_premium_text_rejection_uses_unicode_fallback_without_losing_saved_text(
    studio, monkeypatch
):
    saved = f'<tg-emoji emoji-id="{ICON}">📑</tg-emoji> Result'
    studio.design.set_text("lt", "p.profile_title", saved)
    original = studio.transport.make_request
    rejected = []

    async def send(bot, method, timeout=None):
        if isinstance(method, SendMessage) and "<tg-emoji" in method.text:
            rejected.append(method)
            raise TelegramBadRequest(method, "CUSTOM_EMOJI_INVALID")
        return await original(bot, method, timeout=timeout)

    monkeypatch.setattr(studio.transport, "make_request", send)
    await studio.click(cb("preview", "result_clear"), actor=900)
    assert len(rejected) == 1
    assert any(
        "📑 Result" in c.text and "<tg-emoji" not in c.text
        for c in studio.transport.calls
        if isinstance(c, SendMessage)
    )
    assert studio.design.text("lt", "p.profile_title") == saved
    assert "Telegram Premium" in studio.text()


async def test_unrelated_api_error_is_not_misreported_as_premium_rejection(studio, monkeypatch):
    async def send(bot, method, timeout=None):
        if isinstance(method, SendPhoto):
            raise TelegramBadRequest(method, "PHOTO_INVALID")
        return True

    monkeypatch.setattr(studio.transport, "make_request", send)
    with pytest.raises(TelegramBadRequest, match="PHOTO_INVALID"):
        await studio.click(cb("preview", "home_admin"), actor=900)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_result_category_edits_complete_live_template_with_dynamic_identity(studio, lang):
    assert all(
        key in CATALOGS[lang] and text("result." + key, lang) != "result." + key
        for key in RESULT_KEYS
    )
    await studio.click(cb("lang", lang), actor=900)
    await studio.click(cb("list", "result.0"), actor=900)
    await studio.click(cb("key", value("p.profile_card")), actor=900)
    assert any(
        b.callback_data == cb("preview", "result_clear")
        for row in studio.transport.calls[-1].reply_markup.inline_keyboard
        for b in row
    )
    await studio.click(cb("edit", value("p.profile_card")), actor=900)
    await studio.send(
        "{title}\n{user}\n⭐ REP {score}\n+REP {positive} • -REP {negative}", actor=900
    )
    for key in (
        "p.profile_title",
        "p.lookup_status",
        "p.lookup_clear_status",
        "p.no_scam",
        "p.lookup_identity_known",
        "p.warning",
    ):
        await studio.click(cb("edit", value(key)), actor=900)
        await studio.send("EDIT " + key, actor=900)
    await studio.click(cb("preview", "result_clear"), actor=900)
    result = studio.transport.calls[-1]
    assert isinstance(result, SendMessage)
    assert "@demo_user" in result.text and "123456789" in result.text
    assert "+REP 0 • -REP 0" in result.text
    assert all(
        "EDIT " + key in result.text
        for key in (
            "p.profile_title",
            "p.lookup_status",
            "p.lookup_clear_status",
            "p.no_scam",
            "p.lookup_identity_known",
            "p.warning",
        )
    )
    assert studio.design.text(lang, "p.warning") == "EDIT p.warning"
    assert t("p.warning", lang) == CATALOGS[lang]["p.warning"]


async def test_premium_emoji_entities_in_result_text_keep_utf16_offsets_and_typed_html(studio):
    await studio.click(cb("edit", value("p.warning")), actor=900)
    await studio.send(
        "🛡 <b>Note</b> 📑",
        actor=900,
        entities=[MessageEntity(type="custom_emoji", offset=15, length=2, custom_emoji_id=ICON)],
    )
    assert (
        studio.design.text("lt", "p.warning")
        == f'🛡 <b>Note</b> <tg-emoji emoji-id="{ICON}">📑</tg-emoji>'
    )


async def test_premium_entity_in_button_label_requires_separate_icon_setting(studio):
    await studio.click(cb("edit", value("button.lookup")), actor=900)
    await studio.send(
        "📑 CHECK",
        actor=900,
        entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id=ICON)],
    )
    assert studio.design.text("lt", "button.lookup") == CATALOGS["lt"]["button.lookup"]
    assert await studio.state(900) == Input.text.state


async def test_result_placeholder_removal_and_search_do_not_corrupt_template(studio):
    await studio.click(cb("list", "result.0"), actor=900)
    await studio.click(cb("search", "result"), actor=900)
    await studio.send("p.lookup_identity_known", actor=900)
    assert cb("key", value("p.lookup_identity_known")) in [
        b.callback_data
        for row in studio.transport.calls[-1].reply_markup.inline_keyboard
        for b in row
    ]
    await studio.click(cb("edit", value("p.profile_card")), actor=900)
    await studio.send("Only text", actor=900)
    assert studio.design.text("lt", "p.profile_card") == CATALOGS["lt"]["p.profile_card"]
    assert await studio.state(900) == Input.text.state

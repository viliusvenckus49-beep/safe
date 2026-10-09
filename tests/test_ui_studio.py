"""UI studio edits are persistent and incapable of invoking production moderation."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, BanChatMember, SendDocument, SendMessage, SendPhoto
from pydantic import ValidationError
from test_telegram import Journey, Transport

from app.bot import keyboards as kb
from app.bot.callbacks import Action
from app.i18n import CATALOGS, t
from app.test_ui.__main__ import only_ui_requests
from app.test_ui.access import Access
from app.test_ui.config import UISettings
from app.test_ui.preview import SCREENS, screen
from app.test_ui.profile import KEYS, Design, identifiers, validate_text
from app.test_ui.router import Input, cb, create_router


@pytest_asyncio.fixture
async def studio(tmp_path):
    design = Design(tmp_path / "design.json")
    settings = UISettings(bot_token="654321:TEST_ONLY", admin_ids="900,901")
    access = Access(design.path.with_name("access.json"), settings.admins)
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, design, access))
    journey = Journey(bot, dispatcher, transport)
    journey.design = design
    journey.access = access
    yield journey
    await dispatcher.storage.close()
    await bot.session.close()


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_administrator_adds_editor_by_id_with_immediate_editing_access(studio, lang):
    await studio.send("/start", actor=900)
    await studio.click(cb("lang", lang), actor=900)
    buttons = [b for row in studio.transport.calls[-1].reply_markup.inline_keyboard for b in row]
    assert cb("editors") in [b.callback_data for b in buttons]
    await studio.click(cb("editors"), actor=900)
    await studio.click(cb("add_editor"), actor=900)
    assert await studio.state(900) == Input.editor.state
    await studio.send("8425927753", actor=900)
    assert await studio.state(900) is None
    assert studio.access.can_edit(8425927753)
    assert not studio.access.is_admin(8425927753)
    assert Access(studio.access.path, frozenset({900, 901})).can_edit(8425927753)
    await studio.send("/start", actor=8425927753)
    buttons = [b for row in studio.transport.calls[-1].reply_markup.inline_keyboard for b in row]
    assert cb("editors") not in [b.callback_data for b in buttons]
    await studio.click(cb("edit", value("button.lookup")), actor=8425927753)
    await studio.send("🔎 EDITOR", actor=8425927753)
    assert studio.design.text("lt", "button.lookup") == "🔎 EDITOR"
    await studio.click(cb("export"), actor=8425927753)
    assert b"8425927753" not in studio.transport.calls[-1].document.data


async def test_persisted_test_administrator_has_editor_management_button(studio):
    studio.access.add_admin(8425927753)
    await studio.send("/start", actor=8425927753)
    buttons = [b for row in studio.transport.calls[-1].reply_markup.inline_keyboard for b in row]
    assert cb("editors") in [b.callback_data for b in buttons]
    await studio.click(cb("add_editor"), actor=8425927753)
    await studio.send("888", actor=8425927753)
    assert studio.access.can_edit(888)


async def test_editor_cannot_forge_access_management_or_injected_input_state(studio):
    studio.access.add_editor(888)
    await studio.click(cb("editors"), actor=888)
    assert "900" not in studio.text()
    await studio.click(cb("add_editor"), actor=888)
    assert await studio.state(888) is None
    state = studio.dp.fsm.get_context(bot=studio.bot, chat_id=888, user_id=888)
    await state.set_state(Input.editor)
    await studio.send("777", actor=888)
    assert not studio.access.can_edit(777)
    assert await studio.state(888) is None


@pytest.mark.parametrize(
    "candidate", ["0", "-1", "@username", "8425927753 abc", "９００", "9223372036854775808"]
)
async def test_invalid_editor_id_does_not_grant_or_cancel_input(studio, candidate):
    await studio.click(cb("add_editor"), actor=900)
    await studio.send(candidate, actor=900)
    assert await studio.state(900) == Input.editor.state
    assert studio.access.editors == frozenset()
    await studio.click(cb("home"), actor=900)
    await studio.send("8425927753", actor=900)
    assert not studio.access.can_edit(8425927753)


async def test_duplicate_editor_is_idempotent_and_groups_still_cannot_edit(studio):
    for _ in range(2):
        await studio.click(cb("add_editor"), actor=900)
        await studio.send("888", actor=900)
    assert studio.access.data["editors"] == [888]
    await studio.click(cb("edit", value("button.lookup")), actor=888, chat=-1000)
    await studio.send("GROUP EDIT", actor=888, chat=-1000)
    assert studio.design.data["texts"] == {}


def value(key):
    return str(KEYS.index(key))


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_edit_button_and_home_text_preview_restart_reset(studio, lang):
    await studio.send("/start", actor=900)
    await studio.click(cb("lang", lang), actor=900)
    await studio.click(cb("edit", value("button.lookup")), actor=900)
    assert await studio.state(900) == Input.text.state
    await studio.send("🔎 DEMO CHECK", actor=900)
    assert await studio.state(900) is None
    await studio.click(cb("edit", value("p.home")), actor=900)
    await studio.send("<b>CRIMSON DEMO</b>\n\nDesign preview", actor=900)
    await studio.click(cb("preview", "home_admin"), actor=900)
    preview = studio.transport.calls[-1]
    assert isinstance(preview, SendPhoto)
    assert "CRIMSON DEMO" in preview.caption
    buttons = [b for row in preview.reply_markup.inline_keyboard for b in row]
    lookup = next(b for b in buttons if b.text == "🔎 DEMO CHECK")
    assert lookup.callback_data == Action(name="lookup").pack()
    loaded = Design(studio.design.path)
    assert loaded.text(lang, "button.lookup") == "🔎 DEMO CHECK"
    assert loaded.text(lang, "p.home").startswith("<b>CRIMSON DEMO")
    assert t("button.lookup", lang) == CATALOGS[lang]["button.lookup"]
    await studio.click(cb("reset", value("button.lookup")), actor=900)
    assert Design(studio.design.path).text(lang, "button.lookup") == CATALOGS[lang]["button.lookup"]


async def test_search_language_export_and_navigation_cancel_edit(studio):
    await studio.send("/ui", actor=900)
    await studio.click(cb("search", "texts"), actor=900)
    await studio.send("p.receipt_scam", actor=900)
    await studio.click(cb("key", value("p.receipt_scam")), actor=900)
    assert "heading" in studio.text()
    await studio.click(cb("edit", value("p.receipt_scam")), actor=900)
    await studio.click(cb("home"), actor=900)
    assert await studio.state(900) is None
    await studio.click(cb("export"), actor=900)
    exported = studio.transport.calls[-1]
    assert isinstance(exported, SendDocument)
    assert json.loads(exported.document.data) == studio.design.data
    assert b"bot_token" not in exported.document.data


async def test_placeholders_rejected_without_losing_current_text(studio):
    await studio.send("/ui", actor=900)
    await studio.click(cb("edit", value("p.receipt_scam")), actor=900)
    await studio.send("SCAM {name}", actor=900)
    assert await studio.state(900) == Input.text.state
    assert studio.design.text("lt", "p.receipt_scam") == CATALOGS["lt"]["p.receipt_scam"]
    await studio.send(CATALOGS["lt"]["p.receipt_scam"].replace("⛔️", "🚷"), actor=900)
    assert await studio.state(900) is None


async def test_other_admin_edit_conflict_is_not_silently_overwritten(studio):
    await studio.click(cb("edit", value("button.lookup")), actor=900)
    await studio.click(cb("edit", value("button.lookup")), actor=901)
    await studio.send("SECOND ADMIN", actor=901)
    await studio.send("OLD DRAFT", actor=900)
    assert studio.design.text("lt", "button.lookup") == "SECOND ADMIN"
    assert await studio.state(900) is None


@pytest.mark.parametrize("actor,chat", [(1, None), (900, -1000)])
async def test_nonadministrator_and_group_callbacks_cannot_edit(studio, actor, chat):
    await studio.send("/ui", actor=actor, chat=chat)
    await studio.click(cb("edit", value("button.lookup")), actor=actor, chat=chat)
    await studio.send("HACKED", actor=actor, chat=chat)
    assert studio.design.data["texts"] == {}
    assert await studio.state(actor) is None
    assert not any(isinstance(c, SendPhoto) for c in studio.transport.calls)
    if chat:
        assert not any(isinstance(c, SendMessage) for c in studio.transport.calls)


async def test_preview_moderation_and_commands_are_demo_only(studio):
    await studio.click(cb("preview", "scam"), actor=900)
    for data in ("sa:unban:1", "sa:remove:1", "sa:retry:1", "sc|add_sc|", "sc|del_sc|"):
        await studio.click(data, actor=900)
        assert isinstance(studio.transport.calls[-1], AnswerCallbackQuery)
        assert studio.transport.calls[-1].show_alert is True
    await studio.send("/add_sc 123456789", actor=900)
    assert not any(isinstance(c, BanChatMember) for c in studio.transport.calls)
    assert studio.design.data["texts"] == {}


async def test_transport_blocks_moderation_before_network():
    request = AsyncMock()
    with pytest.raises(RuntimeError):
        await only_ui_requests(request, None, BanChatMember(chat_id=-1000, user_id=42))
    request.assert_not_awaited()
    method = SendMessage(chat_id=900, text="UI preview")
    await only_ui_requests(request, None, method)
    request.assert_awaited_once_with(None, method)


async def test_layout_change_preserves_callbacks_and_admin_button_privacy(studio):
    original = [item for row in identifiers("home") for item in row]
    await studio.click(cb("layout", "home"), actor=900)
    await studio.send("\n".join(str(i) for i in range(len(original), 0, -1)), actor=900)
    with studio.design.preview("lt"):
        admin = studio.design.markup("home")
        user = studio.design.markup("home", admin=False)
    assert [b.callback_data for row in admin.inline_keyboard for b in row] == original[::-1]
    assert Action(name="admin").pack() not in [
        b.callback_data for row in user.inline_keyboard for b in row
    ]
    assert [b.callback_data for row in kb.home().inline_keyboard for b in row] != original[::-1]
    await studio.click(cb("reset_layout", "home"), actor=900)
    assert "home" not in studio.design.data["layouts"]


@pytest.mark.parametrize("name", SCREENS)
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_preview_screens_use_real_builders_and_synthetic_identity(tmp_path, name, lang):
    body, buttons = screen(name, Design(tmp_path / "design.json"), lang)
    assert body and buttons.inline_keyboard
    assert "@demo_user" in body if name in {"scam", "scam_unknown", "trusted", "profile"} else True
    assert all(len(b.callback_data.encode()) <= 64 for row in buttons.inline_keyboard for b in row)


@pytest.mark.parametrize(
    "key,value",
    [
        ("button.lookup", "x" * 65),
        ("button.lookup", "🚷" * 33),
        ("button.lookup", "<b>x</b>"),
        ("button.lookup", "x\ny"),
        ("p.home", "<b>unclosed"),
        ("p.home", "<script>x</script>"),
        ("p.home", '<a href="javascript:alert(1)">x</a>'),
        ("p.home", "{new_field}"),
        ("p.home", "{.__class__}"),
        ("p.home", "A & B"),
        ("p.home", "<!DOCTYPE html>"),
    ],
)
def test_invalid_ui_text_is_rejected(key, value):
    with pytest.raises(ValueError):
        validate_text("lt", key, value)


def test_valid_unicode_html_and_placeholder_format(tmp_path):
    design = Design(tmp_path / "design.json")
    design.set_text("lt", "button.lookup", "🚷 TIKRINTI")
    design.set_text("en", "p.home", '<b>SAFECheck</b> &amp; <a href="https://example.com">Info</a>')
    assert design.path.stat().st_mode & 0o777 == 0o600
    assert Design(design.path).export() == design.export()


def test_failed_atomic_write_retains_memory_and_file(tmp_path, monkeypatch):
    design = Design(tmp_path / "design.json")
    design.set_text("lt", "button.lookup", "FIRST")
    previous = design.export()

    def fail(*args):
        raise OSError("Disk write failed")

    monkeypatch.setattr("app.test_ui.profile.os.replace", fail)
    with pytest.raises(OSError):
        design.set_text("lt", "button.lookup", "SECOND")
    assert design.export() == previous == design.path.read_bytes()
    assert not list(tmp_path.glob(".design-*"))


@pytest.mark.parametrize("value", ["1 1", "1", "0", "-1", "99999", "1 2 3 4", "bad"])
def test_invalid_layout_cannot_drop_duplicate_or_invent_callbacks(tmp_path, value):
    design = Design(tmp_path / "design.json")
    with pytest.raises(ValueError):
        design.set_layout("home", value)
    assert design.data["layouts"] == {}


async def test_overrides_are_scoped_to_preview_tasks(tmp_path):
    design = Design(tmp_path / "design.json")
    design.set_text("lt", "button.lookup", "CUSTOM")
    entered, release = asyncio.Event(), asyncio.Event()

    async def preview_task():
        with design.preview("lt"):
            entered.set()
            await release.wait()
            return t("button.lookup")

    task = asyncio.create_task(preview_task())
    await entered.wait()
    assert t("button.lookup", "lt") == CATALOGS["lt"]["button.lookup"]
    release.set()
    assert await task == "CUSTOM"


def test_configuration_requires_separate_explicit_credentials():
    with pytest.raises(ValidationError):
        UISettings(bot_token="not-a-token", admin_ids="900")
    with pytest.raises(ValidationError):
        UISettings(bot_token="123456:TEST", admin_ids="")


def test_invalid_existing_design_is_not_overwritten(tmp_path):
    path = tmp_path / "design.json"
    path.write_text('{"version": 9, "texts": {}, "layouts": {}}')
    with pytest.raises(ValueError):
        Design(path)
    assert json.loads(path.read_text())["version"] == 9

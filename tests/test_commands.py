"""Command identifiers are stable; menus honor explicit per-user language."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SetMyCommands
from aiogram.types import BotCommandScopeChat

from app.bot.commands import ADMIN, PUBLIC, commands, user_commands
from app.i18n import t


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_command_labels_are_localized_without_changing_identifiers(lang):
    menu = commands(lang)
    assert [item.command for item in menu] == ["start", "ask", "rep", "report"]
    assert [item.command for item in menu] == list(PUBLIC)
    assert all(item.description == t("command." + item.command, lang) for item in menu)
    assert not set(ADMIN).intersection(item.command for item in menu)
    assert [item.command for item in commands(lang, admin=True)] == list(PUBLIC + ADMIN)


@pytest.mark.asyncio
async def test_user_menu_uses_chat_scope_and_saved_language():
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    await user_commands(bot, 42, "en")
    call = bot.set_my_commands.await_args
    assert call.kwargs == {"scope": BotCommandScopeChat(chat_id=42)}
    assert call.args[0] == commands("en")


@pytest.mark.asyncio
async def test_unavailable_command_scope_does_not_block_language_selection():
    bot = SimpleNamespace(
        set_my_commands=AsyncMock(
            side_effect=TelegramBadRequest(
                method=SetMyCommands(commands=[]), message="chat not found"
            )
        )
    )
    await user_commands(bot, 42, "ru")

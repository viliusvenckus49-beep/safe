"""Command identifiers are stable; menus honor explicit per-user language."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SetMyCommands
from aiogram.types import BotCommandScopeAllGroupChats, BotCommandScopeChat
from test_telegram import journey as telegram_journey

from app.bot.commands import ADMIN, GROUP, PUBLIC, commands, group_commands, user_commands
from app.i18n import t
from app.services import Service

journey = telegram_journey


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


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_group_information_command_is_public_and_localized(lang):
    menu = commands(lang, group=True)
    assert [item.command for item in menu] == list(GROUP)
    assert menu[-1].command == "help"
    assert menu[-1].description == t("command.help", lang)
    assert "help" not in [item.command for item in commands(lang)]
    assert not {"admin", "status", "groups", "add_sc", "del_sc"}.intersection(
        item.command for item in menu
    )


async def test_all_groups_get_default_and_three_language_menus():
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    await group_commands(bot)
    calls = bot.set_my_commands.await_args_list
    assert len(calls) == 4
    assert calls[0].args[0] == commands("lt", group=True)
    for call in calls:
        assert call.kwargs["scope"] == BotCommandScopeAllGroupChats()
    assert {call.kwargs.get("language_code") for call in calls} == {None, "lt", "en", "ru"}


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("command", ["/help", "/help@redsafecheckbot"])
async def test_group_menu_information_uses_existing_instruction_without_buttons(
    journey, database, settings, lang, command
):
    async with database() as session:
        await Service(settings, session).set_language(1, lang)
    await journey.send(command, chat=-100)
    instruction = journey.transport.calls[-1]
    instruction_id = len(journey.transport.calls)
    assert instruction.text == t("p.info", lang)
    assert instruction.reply_markup is None
    await journey.send("/ask 42", chat=-100)
    assert instruction_id not in [call.message_id for call in journey.transport.deletions]

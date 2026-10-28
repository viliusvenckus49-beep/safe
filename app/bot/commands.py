"""Localized Telegram command menus; command identifiers remain stable."""

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeChat,
)

from app.i18n import LANGUAGES, t

PUBLIC = ("start", "ask", "report")
GROUP = (*PUBLIC, "help")
ADMIN: tuple[str, ...] = ()


def commands(lang: str, *, admin: bool = False, group: bool = False) -> list[BotCommand]:
    return [
        BotCommand(command=name, description=t(f"command.{name}", lang))
        for name in (GROUP if group else PUBLIC) + (ADMIN if admin else ())
    ]


async def group_commands(bot: Bot) -> None:
    scope = BotCommandScopeAllGroupChats()
    await bot.set_my_commands(commands("lt", group=True), scope=scope)
    for lang in sorted(LANGUAGES):
        await bot.set_my_commands(commands(lang, group=True), scope=scope, language_code=lang)
    # Replace the previously registered administrator menus too: changing only
    # the public scope leaves Telegram's more-specific old menu visible.
    # Hidden commands keep their existing handlers and permission checks.
    admin_scope = BotCommandScopeAllChatAdministrators()
    for selected in (None, *sorted(LANGUAGES)):
        await bot.set_my_commands(
            commands(selected or "lt", group=True), scope=admin_scope, language_code=selected
        )


async def user_commands(bot: Bot, actor: int, lang: str, *, admin: bool = False) -> None:
    # A chat-specific menu without language_code takes precedence over global
    # Telegram-client language menus, honoring the stored SAFECheck choice.
    try:
        await bot.set_my_commands(
            commands(lang, admin=admin), scope=BotCommandScopeChat(chat_id=actor)
        )
    except TelegramAPIError as error:
        structlog.get_logger().warning(
            "user_command_scope_unavailable", user_id=actor, exception_type=type(error).__name__
        )

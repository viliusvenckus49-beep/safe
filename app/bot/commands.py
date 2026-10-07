"""Localized Telegram command menus; command identifiers remain stable."""

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeChat

from app.i18n import t

PUBLIC = ("start", "ask", "rep", "report")
ADMIN: tuple[str, ...] = ()


def commands(lang: str, *, admin: bool = False) -> list[BotCommand]:
    return [
        BotCommand(command=name, description=t(f"command.{name}", lang))
        for name in PUBLIC + (ADMIN if admin else ())
    ]


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

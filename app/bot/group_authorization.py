"""Fresh Telegram rights checks before explicit owner approval."""

from aiogram import Bot

from app.config import Settings
from app.services import DomainError


async def verify_group(bot: Bot, settings: Settings, actor: int, chat_id: int):
    if settings.group_owner is None or actor != settings.group_owner:
        raise DomainError("forbidden")
    chat = await bot.get_chat(chat_id)
    if chat.type not in {"group", "supergroup"}:
        raise DomainError("invalid_input")
    owner = await bot.get_chat_member(chat_id, actor)
    member = await bot.get_chat_member(chat_id, bot.id)
    if owner.status not in {"creator", "administrator"}:
        raise DomainError("group_owner_rights")
    if member.status != "administrator" or not getattr(member, "can_restrict_members", False):
        raise DomainError("group_bot_rights")
    return chat

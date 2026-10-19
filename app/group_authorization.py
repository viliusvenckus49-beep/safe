"""Live chat-scoped permissions, independent of SAFECheck's global administrator ACL."""

import asyncio
from typing import Any

from app.errors import DomainError

RIGHTS_TIMEOUT = 5


def numeric_id(value: int, *, chat: bool = False) -> None:
    if (
        type(value) is not int
        or not -(2**63) < value < 2**63
        or (value >= 0 if chat else value <= 0)
    ):
        raise DomainError("group_moderation_invalid_id")


class GroupAuthorizer:
    def __init__(self, bot: Any):
        self.bot = bot

    async def member(self, chat_id: int, user_id: int) -> Any:
        numeric_id(chat_id, chat=True)
        numeric_id(user_id)
        member = await asyncio.wait_for(self.bot.get_chat_member(chat_id, user_id), RIGHTS_TIMEOUT)
        if getattr(getattr(member, "user", None), "id", None) != user_id:
            raise DomainError("group_moderation_identity_mismatch")
        return member

    async def actor(self, chat_id: int, actor_id: int, *, configure: bool = False) -> None:
        if actor_id == self.bot.id:
            raise DomainError("group_moderation_forbidden")
        member = await self.member(chat_id, actor_id)
        if getattr(member.user, "is_bot", True):
            raise DomainError("group_moderation_forbidden")
        if member.status == "creator":
            return
        if (
            member.status != "administrator"
            or getattr(member, "can_restrict_members", False) is not True
        ):
            raise DomainError("group_moderation_forbidden")
        if configure and getattr(member, "can_change_info", False) is not True:
            raise DomainError("group_moderation_forbidden")

    async def bot_rights(self, chat_id: int) -> None:
        member = await self.member(chat_id, self.bot.id)
        if (
            member.status != "administrator"
            or getattr(member, "can_restrict_members", False) is not True
        ):
            raise DomainError("group_moderation_bot_rights")

    async def target(self, chat_id: int, target_id: int) -> Any:
        if target_id == self.bot.id:
            raise DomainError("group_moderation_protected_target")
        member = await self.member(chat_id, target_id)
        if member.status in {"creator", "administrator"}:
            raise DomainError("group_moderation_protected_target")
        return member

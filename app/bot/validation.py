"""Shared validation for Telegram actors and public target identifiers."""

import re

from aiogram.types import Message

from app.services import DomainError

TARGET = re.compile(r"^(?:@[A-Za-z][A-Za-z0-9_]{4,31}|[1-9][0-9]{0,18})$")


def actor_id(message: Message) -> int:
    if message.from_user is None or message.from_user.is_bot or message.sender_chat:
        raise DomainError("forbidden")
    return message.from_user.id


def valid_target(value: str) -> bool:
    return bool(TARGET.fullmatch(value.strip()))

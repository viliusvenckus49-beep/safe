"""Preserve translated native icons in every inline keyboard builder."""

from typing import Any

from aiogram.types import InlineKeyboardButton


def inline_button(label: str, **values: Any) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=str(label), icon_custom_emoji_id=getattr(label, "icon_custom_emoji_id", None), **values
    )

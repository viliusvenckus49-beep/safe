from collections.abc import Sequence
from typing import Any

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.buttons import inline_button
from app.i18n import t


class GroupAction(CallbackData, prefix="sg", sep="|"):
    action: str
    chat_id: int = 0
    nonce: str = ""


def button(text: str, action: str, chat_id: int = 0, nonce: str = "") -> InlineKeyboardButton:
    return inline_button(
        text, callback_data=GroupAction(action=action, chat_id=chat_id, nonce=nonce).pack()
    )


def menu(groups: Sequence[Any], *, admin: bool) -> InlineKeyboardMarkup:
    rows = [[button(group.title[:55], "group", group.chat_id)] for group in groups]
    rows.append([inline_button(t("button.back"), callback_data="sc|admin_section|owner")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group(chat_id: int, *, approved: bool = True) -> InlineKeyboardMarkup:
    if not approved:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [button(t("button.group_approve"), "approve", chat_id)],
                [button(t("button.group_remove"), "remove", chat_id)],
                [button(t("button.back"), "list")],
            ]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(t("button.group_rights"), "approve", chat_id)],
            [button(t("button.group_remove"), "remove", chat_id)],
            [button(t("button.back"), "list")],
        ]
    )


def back_to_group(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button(t("button.back"), "group", chat_id)]])


def remove_confirmation(chat_id: int, nonce: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button(t("button.group_remove_confirm"), "remove_confirm", chat_id, nonce)],
            [button(t("button.back"), "group", chat_id)],
        ]
    )


def enrollment(chat_id: int, *, approved: bool) -> InlineKeyboardMarkup:
    rows = [] if approved else [[button(t("button.group_approve"), "approve", chat_id)]]
    if not approved:
        rows.append([button(t("button.back"), "list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

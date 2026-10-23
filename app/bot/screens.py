"""Move navigation panels to the chat bottom and retire their predecessors."""

import re
from contextvars import ContextVar
from html import unescape
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

from app.bot.keyboards import back

navigation_edit: ContextVar[bool] = ContextVar("navigation_edit", default=False)
panel_state: ContextVar[FSMContext | None] = ContextVar("panel_state", default=None)
preserved_source: ContextVar[int | None] = ContextVar("preserved_source", default=None)
HOME_PHOTO = Path(__file__).resolve().parents[1] / "assets" / "home.jpg"


async def send_screen(
    message: Message,
    text: str,
    markup: InlineKeyboardMarkup | None,
    home_photo: bool,
    *,
    persistent: bool = False,
) -> Message:
    if getattr(message.chat, "type", None) == "private" and (
        markup is None or not markup.inline_keyboard
    ):
        markup = back("home", "receipt" if persistent else "")
    if home_photo:
        return await message.answer_photo(
            FSInputFile(HOME_PHOTO), caption=text, reply_markup=markup
        )
    return await message.answer(text, reply_markup=markup)


def _unavailable(error: TelegramBadRequest) -> bool:
    reason = error.message.lower()
    return any(
        value in reason
        for value in (
            "message to edit not found",
            "message can't be edited",
            "message_id_invalid",
            "there is no text in the message to edit",
            "message caption is too long",
        )
    )


async def render(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    edit: bool = True,
    home_photo: bool = False,
    interactive: bool = False,
) -> Message | bool:
    state = panel_state.get()
    media = any(
        getattr(message, kind, None)
        for kind in ("photo", "video", "animation", "document", "audio", "voice")
    )
    caption_fits = len(unescape(re.sub(r"<[^>]+>", "", text)).encode("utf-16-le")) // 2 <= 1024
    previous = (
        (await state.get_data()).get("screen_message_id")
        if state is not None and message.message_id == preserved_source.get()
        else None
    )
    if (
        edit
        and (navigation_edit.get() or interactive)
        and not home_photo
        and (message.chat.type == "private" or interactive)
        and message.message_id != preserved_source.get()
        and (not media or (interactive and caption_fits))
    ):
        try:
            if interactive and media:
                result = await message.edit_caption(caption=text, reply_markup=reply_markup)
            else:
                result = await message.edit_text(text, reply_markup=reply_markup)
            if state is not None:
                await state.update_data(screen_message_id=message.message_id)
            return result
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.lower():
                return message
            if not _unavailable(error):
                raise
    result = await send_screen(message, text, reply_markup, home_photo)
    if state is not None:
        await state.update_data(screen_message_id=result.message_id)
    if edit and message.message_id != preserved_source.get():
        await delete_panel(message, message.message_id)
    elif edit and previous and previous not in {message.message_id, result.message_id}:
        await delete_panel(message, previous)
    return result


async def flow_screen(
    message: Message,
    state: FSMContext,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    home_photo: bool = False,
    persistent: bool = False,
) -> None:
    data = await state.get_data()
    previous = data.get("screen_message_id")
    if (
        navigation_edit.get()
        and not persistent
        and previous == message.message_id
        and message.message_id != preserved_source.get()
        and message.chat.type == "private"
        and not home_photo
        and not message.photo
    ):
        await render(message, text, reply_markup=reply_markup)
        return
    result = await send_screen(message, text, reply_markup, home_photo, persistent=persistent)
    # Receipts are chat history, never an anchor retired by the next screen.
    await state.update_data(screen_message_id=None if persistent else result.message_id)
    if previous and previous != result.message_id and previous != preserved_source.get():
        await delete_panel(message, previous)


async def clear_flow(state: FSMContext) -> None:
    """Discard drafts but keep the current UI anchor between explicit commands."""
    data = await state.get_data()
    await state.clear()
    if data.get("screen_message_id"):
        await state.update_data(screen_message_id=data["screen_message_id"])


async def close_panel(message: Message, state: FSMContext, *, callback: bool = False) -> None:
    data = await state.get_data()
    await state.clear()
    message_id = message.message_id if callback else data.get("screen_message_id")
    if not message_id:
        return
    await delete_panel(message, message_id)


async def delete_panel(message: Message, message_id: int) -> None:
    assert message.bot is not None
    try:
        await message.bot.delete_message(message.chat.id, message_id)
    except TelegramBadRequest as error:
        if "message to delete not found" in error.message.lower():
            return
        if (
            "can't be deleted" not in error.message.lower()
            and "cannot be deleted" not in error.message.lower()
        ):
            raise
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id, message_id=message_id, reply_markup=None
            )
        except TelegramBadRequest as edit_error:
            if "message is not modified" not in edit_error.message.lower() and not _unavailable(
                edit_error
            ):
                raise

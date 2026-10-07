"""Move navigation panels to the chat bottom and retire their predecessors."""

from contextvars import ContextVar
from pathlib import Path

from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, InlineKeyboardMarkup, Message

panel_state: ContextVar[FSMContext | None] = ContextVar("panel_state", default=None)
preserved_source: ContextVar[int | None] = ContextVar("preserved_source", default=None)
HOME_PHOTO = Path(__file__).resolve().parents[1] / "assets" / "home.jpg"


async def send_screen(
    message: Message, text: str, markup: InlineKeyboardMarkup | None, home_photo: bool
) -> Message:
    if home_photo:
        return await message.answer_photo(
            FSInputFile(HOME_PHOTO), caption=text, reply_markup=markup
        )
    return await message.answer(text, reply_markup=markup)


def _unavailable(error: TelegramBadRequest) -> bool:
    reason = error.message.lower()
    return any(
        value in reason
        for value in ("message to edit not found", "message can't be edited", "message_id_invalid")
    )


async def render(
    message: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    edit: bool = True,
    home_photo: bool = False,
) -> Message | bool:
    state = panel_state.get()
    previous = (
        (await state.get_data()).get("screen_message_id")
        if state is not None and message.message_id == preserved_source.get()
        else None
    )
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
    result = await send_screen(message, text, reply_markup, home_photo)
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

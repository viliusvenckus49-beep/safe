"""Native Ephemeral panel lifecycle; public group messages are never edited here."""

from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup, Message

from app.errors import DomainError


def ephemeral_id(message: Message, actor: int) -> int | None:
    value = message.ephemeral_message_id
    if (
        value is not None
        and message.receiver_user is not None
        and message.receiver_user.id != actor
    ):
        raise DomainError("group_moderation_forbidden")
    return value if type(value) is int and value > 0 else None


def private_message_key(message: Message) -> int:
    # Existing receipt storage has a single signed ID column. Positive IDs are
    # ordinary messages; negative IDs identify native ephemeral messages, whose
    # ordinary message_id is always zero. PRIVATE visibility supplies the kind.
    return -message.ephemeral_message_id if message.ephemeral_message_id else message.message_id


async def show(
    message: Message, actor: int, text: str, markup: InlineKeyboardMarkup | None, *, edit: bool
) -> Message | None:
    from app.bot.moderation_errors import deliver_error

    bot = message.bot
    if bot is None:
        raise RuntimeError("Message has no bot context")
    private_id = ephemeral_id(message, actor)
    if edit and private_id is not None:
        try:
            await bot.edit_ephemeral_message_text(
                chat_id=message.chat.id,
                receiver_user_id=actor,
                ephemeral_message_id=private_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return message.model_copy(update={"text": text, "reply_markup": markup}).as_(bot)
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.lower():
                return message
            # An explicitly expired/rejected private panel may be replaced by
            # a fresh PRIVATE panel; never switch it to a public group reply.
        except TelegramForbiddenError:
            pass
        except (TelegramAPIError, TimeoutError, OSError):
            return None  # Unknown edit delivery: do not add a second window.
    elif (
        edit
        and message.chat.type == "private"
        and message.chat.id == actor
        and message.message_id > 0
    ):
        try:
            result = await bot.edit_message_text(
                chat_id=actor,
                message_id=message.message_id,
                text=text,
                parse_mode="HTML",
                reply_markup=markup,
            )
            return result if isinstance(result, Message) else message
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.lower():
                return message
        except TelegramForbiddenError:
            pass
        except (TelegramAPIError, TimeoutError, OSError):
            return None
    return await deliver_error(
        message, actor, text, "/unwarn", reply_markup=markup, allow_public_hint=False
    )


async def dismiss(message: Message, actor: int) -> bool:
    """Cleanup is best effort and cannot turn a committed action into a failure."""
    private_id = ephemeral_id(message, actor)
    bot = message.bot
    if bot is None:
        return False
    try:
        if private_id is not None:
            await bot.delete_ephemeral_message(
                chat_id=message.chat.id, receiver_user_id=actor, ephemeral_message_id=private_id
            )
        elif message.chat.type == "private" and message.chat.id == actor and message.message_id > 0:
            await bot.delete_message(actor, message.message_id)
        else:
            return False  # Never delete a public receipt or guess the ID of an old panel.
        return True
    except (TelegramAPIError, TimeoutError, OSError):
        # If deletion is unavailable, disabling the private keyboard is enough
        # to retire its UI. Never use a normal group edit as a fallback.
        try:
            if private_id is not None:
                await bot.edit_ephemeral_message_reply_markup(
                    chat_id=message.chat.id,
                    receiver_user_id=actor,
                    ephemeral_message_id=private_id,
                    reply_markup=None,
                )
            elif (
                message.chat.type == "private"
                and message.chat.id == actor
                and message.message_id > 0
            ):
                await bot.edit_message_reply_markup(
                    chat_id=actor, message_id=message.message_id, reply_markup=None
                )
        except (TelegramAPIError, TimeoutError, OSError):
            pass
        return False


async def dismiss_stored(bot: Any, chat: int, actor: int, message_key: int | None) -> None:
    if not message_key:
        return  # Legacy message_id=0 carries no recoverable ephemeral ID.
    try:
        if message_key < 0:
            await bot.delete_ephemeral_message(
                chat_id=chat, receiver_user_id=actor, ephemeral_message_id=-message_key
            )
        elif chat == actor:
            await bot.delete_message(chat, message_key)
    except (TelegramAPIError, TimeoutError, OSError):
        pass


async def acknowledge(callback: Any, text: str | None = None, *, alert: bool = False) -> None:
    try:
        await callback.answer(text, show_alert=alert)
    except (TelegramAPIError, TimeoutError, OSError):
        pass

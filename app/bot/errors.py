"""Resolve locale independently after middleware unwinds, including database failures."""

from typing import Any

from aiogram.types import Update

from app import presentation as p
from app.bot.moderation_notices import notify_ban_failure
from app.group_services import GroupService
from app.i18n import use_language
from app.repositories import Repository


async def error_response(update: Update, sessions: Any, settings: Any = None) -> None:
    callback, message = update.callback_query, update.message
    source = callback or message
    actor = getattr(source, "from_user", None)
    if message and getattr(getattr(message, "chat", None), "type", None) in {"group", "supergroup"}:
        # Background enforcement errors must never become public group spam.
        if settings is not None and actor is not None and message.bot is not None:
            async with sessions() as session:
                await notify_ban_failure(
                    message.bot,
                    GroupService(settings, session),
                    message.chat.id,
                    actor.id,
                    result="UpdateError",
                    present=True,
                )
        return
    selected = getattr(actor, "language_code", None)
    try:
        async with sessions() as session:
            selected = await Repository(session).language(actor.id) if actor else None
    except Exception:
        pass  # Safe response remains possible when the database is unavailable.
    with use_language(selected):
        if callback:
            await callback.answer(p.text("error"), show_alert=True)
        elif message:
            await message.answer(p.text("error"))

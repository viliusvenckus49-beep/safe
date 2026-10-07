"""Resolve locale independently after middleware unwinds, including database failures."""

from typing import Any

from aiogram.types import Update

from app import presentation as p
from app.i18n import use_language
from app.repositories import Repository


async def error_response(update: Update, sessions: Any) -> None:
    callback, message = update.callback_query, update.message
    source = callback or message
    actor = getattr(source, "from_user", None)
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

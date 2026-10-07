"""Early throttling and anonymous-sender rejection use persisted preferences."""

from datetime import UTC, datetime
from time import monotonic
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from app.bot.middleware import ServiceMiddleware
from app.i18n import language, t
from app.services import Service


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("anonymous", [False, True])
async def test_early_rejection_uses_saved_language_and_resets_context(
    database, settings, monkeypatch, lang, anonymous
):
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    response = AsyncMock()
    monkeypatch.setattr(Message, "answer", response)
    message = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat(id=42, type="private"),
        from_user=User(id=42, first_name="Tester", is_bot=False),
        text="/ask 43",
        sender_chat=Chat(id=-100, type="channel") if anonymous else None,
    )
    middleware = ServiceMiddleware(settings, database)
    middleware.recent[42] = [monotonic()] * 20
    handler = AsyncMock()
    await middleware(handler, message, {})
    assert response.await_args.args[0] == t(
        "error.anonymous_identity" if anonymous else "p.cooldown", lang
    )
    handler.assert_not_awaited()
    assert language.get() == "lt"

"""Exception responses honor the saved language after middleware has reset its context."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot.errors import error_response
from app.i18n import language, t, use_language
from app.services import Service


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("callback", [False, True])
async def test_error_response_reads_saved_locale_outside_update_context(
    database, settings, lang, callback
):
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    source = SimpleNamespace(
        from_user=SimpleNamespace(id=42, language_code="ru"), answer=AsyncMock()
    )
    update = SimpleNamespace(
        callback_query=source if callback else None, message=None if callback else source
    )
    with use_language("lt"):
        await error_response(update, database)
        assert language.get() == "lt"
    source.answer.assert_awaited_once_with(
        t("p.error", lang), **({"show_alert": True} if callback else {})
    )


@pytest.mark.asyncio
async def test_database_failure_still_has_safe_localized_error_response():
    def unavailable():
        raise RuntimeError("PASSWORD_SENTINEL")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42, language_code="en"), answer=AsyncMock()
    )
    await error_response(SimpleNamespace(callback_query=None, message=message), unavailable)
    message.answer.assert_awaited_once_with(t("p.error", "en"))
    assert "PASSWORD_SENTINEL" not in str(message.answer.await_args)


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_group_failure_never_posts_generic_error_even_if_database_is_down(chat_type):
    def unavailable():
        raise RuntimeError("DATABASE_PASSWORD_SENTINEL")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(type=chat_type),
        answer=AsyncMock(),
    )
    await error_response(SimpleNamespace(callback_query=None, message=message), unavailable)
    message.answer.assert_not_awaited()

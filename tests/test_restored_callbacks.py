from datetime import UTC, datetime, timedelta

import pytest
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.bot.callbacks import Action
from app.i18n import use_language
from app.models import AuditEvent
from app.models import User as DatabaseUser

journey = telegram_journey


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_old_callback_is_rejected_after_restore_and_start_opens_new_menu(
    journey, database, lang
):
    async with database() as session:
        session.add(DatabaseUser(telegram_id=1, language=lang, display_name="Tester"))
        session.add(
            AuditEvent(
                actor_id=900,
                action="legacy_database_import",
                details={"source_digest": "test"},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    callback = CallbackQuery(
        id="old",
        from_user=User(id=1, is_bot=False, first_name="Tester"),
        chat_instance="test",
        data=Action(name="profile", value="u:999").pack(),
        message=Message(
            message_id=1,
            date=datetime.now(UTC) - timedelta(days=1),
            chat=Chat(id=1, type="private"),
        ),
    )
    await journey.dp.feed_update(journey.bot, Update(update_id=99, callback_query=callback))
    alert = next(call for call in journey.transport.calls if isinstance(call, AnswerCallbackQuery))
    with use_language(lang):
        assert alert.text == p.text("data_refreshed")
    assert alert.show_alert
    await journey.send("/start")
    assert journey.text()
    await journey.click(Action(name="profile", value="").pack())
    assert not any(
        isinstance(call, AnswerCallbackQuery) and call.text == p.text("data_refreshed")
        for call in journey.transport.calls[2:]
    )


@pytest.mark.asyncio
async def test_callback_without_import_marker_still_works(journey):
    await journey.send("/start")
    await journey.click(Action(name="profile", value="").pack())
    assert p.text("data_refreshed") not in journey.text()

"""Photo menus share the existing screen lifecycle and localized captions."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendPhoto
from aiogram.types import FSInputFile
from test_telegram import journey as telegram_journey

from app.bot.callbacks import Action
from app.bot.screens import HOME_PHOTO
from app.i18n import t
from app.services import Service

journey = telegram_journey


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
@pytest.mark.parametrize("chat", [None, -100])
async def test_home_photo_caption_buttons_replace_and_close(
    journey, database, settings, locale, chat
):
    async with database() as session:
        await Service(settings, session).set_language(1, locale)
    await journey.send("/start", chat=chat)
    photo = journey.transport.calls[-1]
    assert isinstance(photo, SendPhoto)
    assert isinstance(photo.photo, FSInputFile)
    assert photo.photo.path == HOME_PHOTO
    assert photo.caption == t("p.home", lang=locale)
    assert len(photo.caption.encode("utf-16-le")) // 2 <= 1024
    assert photo.reply_markup is not None
    await journey.click(Action(name="info").pack(), chat=chat)
    assert journey.transport.deletions[-1].message_id == 100
    await journey.click(Action(name="home").pack(), chat=chat)
    assert isinstance(journey.transport.calls[-1], SendPhoto)
    count = len(journey.transport.calls)
    await journey.click(Action(name="close").pack(), chat=chat)
    assert journey.transport.deletions[-1].message_id == 100
    assert len(journey.transport.calls) == count + 1  # callback acknowledgement only


async def test_failed_photo_send_keeps_previous_screen(journey, database, settings, monkeypatch):
    async with database() as session:
        await Service(settings, session).set_language(1, "lt")
    await journey.send("/start")
    previous = (await journey.data())["screen_message_id"]
    deletions = len(journey.transport.deletions)
    original = journey.transport.make_request

    async def reject_photo(bot, method, timeout=None):
        if isinstance(method, SendPhoto):
            raise TelegramBadRequest(method=method, message="Photo upload failed")
        return await original(bot, method, timeout)

    monkeypatch.setattr(journey.transport, "make_request", reject_photo)
    with pytest.raises(TelegramBadRequest):
        await journey.send("/start")
    assert (await journey.data())["screen_message_id"] == previous
    assert len(journey.transport.deletions) == deletions


def test_banner_is_bundled_jpeg():
    assert HOME_PHOTO.is_file()
    assert HOME_PHOTO.read_bytes().startswith(b"\xff\xd8\xff")
    assert HOME_PHOTO.stat().st_size < 10 * 1024 * 1024

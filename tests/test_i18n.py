"""Localization persistence and real Dispatcher journeys without live Telegram access."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    SendMessage,
    SendPhoto,
    SetMyCommands,
)
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TelegramUser
from sqlalchemy import select

from app.bot.callbacks import Action
from app.bot.handlers import create_router
from app.models import User
from app.services import DomainError, Service


class LocalizationTransport(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, (AnswerCallbackQuery, DeleteMessage, SetMyCommands)):
            return True
        return Message(
            message_id=1000 + len(self.calls),
            date=datetime.now(UTC),
            chat=Chat(id=int(method.chat_id), type="private"),
            text=getattr(method, "text", None),
        )


@pytest_asyncio.fixture
async def localized_bot(database, settings):
    transport = LocalizationTransport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(create_router(settings, database))
    yield bot, dispatcher, transport
    await dispatcher.storage.close()
    await bot.session.close()


async def send(localized_bot, text, actor=1, chat=None):
    bot, dispatcher, transport = localized_bot
    message = Message(
        message_id=len(transport.calls) + 1,
        date=datetime.now(UTC),
        chat=Chat(id=chat or actor, type="supergroup" if chat else "private"),
        from_user=TelegramUser(id=actor, is_bot=False, first_name="Tester"),
        text=text,
    )
    await dispatcher.feed_update(bot, Update(update_id=message.message_id, message=message))


async def click(localized_bot, data, actor=1):
    bot, dispatcher, transport = localized_bot
    callback = CallbackQuery(
        id=str(len(transport.calls) + 1),
        from_user=TelegramUser(id=actor, is_bot=False, first_name="Tester"),
        chat_instance="localization",
        data=data,
        message=Message(
            message_id=100, date=datetime.now(UTC), chat=Chat(id=actor, type="private")
        ),
    )
    await dispatcher.feed_update(
        bot, Update(update_id=len(transport.calls) + 1, callback_query=callback)
    )


def last_screen(localized_bot):
    call = next(
        call
        for call in reversed(localized_bot[2].calls)
        if isinstance(call, (SendMessage, SendPhoto))
    )
    return (
        SimpleNamespace(text=call.caption, reply_markup=call.reply_markup)
        if isinstance(call, SendPhoto)
        else call
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["lt", "en", "ru"])
async def test_language_preference_persists_and_survives_identity_metadata_changes(
    database, settings, language
):
    async with database() as session:
        service = Service(settings, session)
        await service.observe(1, "first_name", "First")
        await service.set_language(1, language)
        await service.observe(1, "second_name", "Second")
    async with database() as session:
        user = (await session.scalars(select(User).where(User.telegram_id == 1))).one()
        assert user.language == language
        assert user.username == "second_name"
        other = await Service(settings, session).observe(2, None, "Other")
        assert other.language is None


@pytest.mark.asyncio
@pytest.mark.parametrize("language", ["", "de", "EN", "en<script>", "en:lt"])
async def test_invalid_language_does_not_overwrite_saved_preference(database, settings, language):
    async with database() as session:
        service = Service(settings, session)
        await service.observe(1, None, "Tester")
        await service.set_language(1, "en")
        with pytest.raises(DomainError):
            await service.set_language(1, language)
    async with database() as session:
        user = (await session.scalars(select(User).where(User.telegram_id == 1))).one()
        assert user.language == "en"


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_first_start_picker_selection_saved_and_second_start_home(
    localized_bot, database, lang
):
    await send(localized_bot, "/start")
    picker = last_screen(localized_bot)
    callbacks = {
        button.callback_data for row in picker.reply_markup.inline_keyboard for button in row
    }
    assert {"language:lt", "language:en", "language:ru"} <= callbacks
    await click(localized_bot, f"language:{lang}")
    async with database() as session:
        user = (await session.scalars(select(User).where(User.telegram_id == 1))).one()
        assert user.language == lang
    await send(localized_bot, "/start")
    home = last_screen(localized_bot)
    assert "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™" in home.text
    assert any(
        button.callback_data == Action(name="language").pack()
        for row in home.reply_markup.inline_keyboard
        for button in row
    )
    assert not any(
        button.callback_data == "language:lt"
        for row in home.reply_markup.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_language_menu_change_persists_and_clears_active_wizard(localized_bot, database):
    await send(localized_bot, "/start")
    await click(localized_bot, "language:lt")
    await send(localized_bot, "/report")
    bot, dispatcher, _ = localized_bot
    state = dispatcher.fsm.get_context(bot=bot, chat_id=1, user_id=1)
    assert await state.get_state() is not None
    await click(localized_bot, Action(name="language").pack())
    await click(localized_bot, "language:en")
    assert await state.get_state() is None
    async with database() as session:
        assert (
            await session.scalars(select(User).where(User.telegram_id == 1))
        ).one().language == "en"
    await send(localized_bot, "/language")
    assert any(
        button.callback_data == "language:ru"
        for row in last_screen(localized_bot).reply_markup.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
async def test_invalid_language_callback_cannot_replace_preference(localized_bot, database):
    await send(localized_bot, "/start")
    await click(localized_bot, "language:en")
    await click(localized_bot, "language:de")
    async with database() as session:
        assert (
            await session.scalars(select(User).where(User.telegram_id == 1))
        ).one().language == "en"


@pytest.mark.asyncio
async def test_each_user_receives_own_help_language_without_context_leak(localized_bot):
    await send(localized_bot, "/start", actor=1)
    await click(localized_bot, "language:en", actor=1)
    await send(localized_bot, "/start", actor=2)
    await click(localized_bot, "language:ru", actor=2)
    await send(localized_bot, "/help", actor=1)
    english = last_screen(localized_bot).text
    await send(localized_bot, "/help", actor=2)
    russian = last_screen(localized_bot).text
    await send(localized_bot, "/help", actor=1)
    assert last_screen(localized_bot).text == english
    assert english != russian
    assert not any("а" <= char.lower() <= "я" for char in english)
    assert any("а" <= char.lower() <= "я" for char in russian)


def test_language_context_defaults_and_resets_after_exception():
    from app.i18n import language, use_language

    assert language.get() == "lt"
    with pytest.raises(RuntimeError), use_language("ru"):
        assert language.get() == "ru"
        raise RuntimeError("test")
    assert language.get() == "lt"
    with use_language("en"):
        with use_language("lt"):
            assert language.get() == "lt"
        assert language.get() == "en"
    assert language.get() == "lt"


@pytest.mark.asyncio
async def test_language_context_isolated_between_concurrent_tasks():
    import asyncio

    from app.i18n import language, use_language

    async def worker(lang):
        with use_language(lang):
            await asyncio.sleep(0)
            return language.get()

    assert await asyncio.gather(worker("en"), worker("ru")) == ["en", "ru"]
    assert language.get() == "lt"


def test_catalog_keys_and_named_placeholders_match_all_languages():
    from string import Formatter

    from app.i18n import CATALOGS

    def fields(template):
        return {field for _, field, _, _ in Formatter().parse(template) if field is not None}

    baseline = CATALOGS["lt"]
    for lang in ("en", "ru"):
        assert CATALOGS[lang].keys() == baseline.keys()
        for key, template in baseline.items():
            assert fields(CATALOGS[lang][key]) == fields(template), (lang, key)


def test_translation_explicit_language_unknown_key_and_invalid_language_fallback(monkeypatch):
    from app.i18n import CATALOGS, t, use_language

    monkeypatch.setitem(CATALOGS["lt"], "test.placeholder", "Value {value}")
    monkeypatch.setitem(CATALOGS["en"], "test.placeholder", "English {value}")
    with use_language("ru"):
        assert t("test.placeholder", value="text") == "Value text"
        assert t("test.placeholder", lang="en", value="text") == "English text"
        assert t("test.placeholder", lang="invalid", value="text") == "Value text"
        assert t("key.does.not.exist") == CATALOGS["ru"]["core.error"]
        assert t("test.placeholder") == CATALOGS["ru"]["core.error"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial,target",
    [("lt", "en"), ("lt", "ru"), ("en", "lt"), ("en", "ru"), ("ru", "lt"), ("ru", "en")],
)
async def test_all_language_switch_pairs(localized_bot, database, initial, target):
    from app.i18n import t

    await send(localized_bot, "/start")
    await click(localized_bot, f"language:{initial}")
    await send(localized_bot, "/language")
    await click(localized_bot, f"language:{target}")
    assert last_screen(localized_bot).text == t("language.changed", lang=target) + "\n\n" + t(
        "p.home", lang=target
    )
    async with database() as session:
        assert (
            await session.scalars(select(User).where(User.telegram_id == 1))
        ).one().language == target


@pytest.mark.asyncio
async def test_preexisting_user_without_preference_gets_picker(localized_bot, database, settings):
    async with database() as session:
        await Service(settings, session).observe(1, "existing_user", "Existing")
    await send(localized_bot, "/start")
    assert any(
        button.callback_data == "language:en"
        for row in last_screen(localized_bot).reply_markup.inline_keyboard
        for button in row
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_localized_profile_pending_rep_report_and_admin_permissions(
    localized_bot, database, lang
):
    from app.bot.callbacks import ReportStep
    from app.i18n import t
    from app.models import Report, ReputationRequest

    await send(localized_bot, "/start")
    await click(localized_bot, f"language:{lang}")
    await send(localized_bot, "/profile")
    assert "𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™" in last_screen(localized_bot).text
    assert "Tester" in last_screen(localized_bot).text
    await send(localized_bot, "+rep 42 Test comment")
    async with database() as session:
        request = (await session.scalars(select(ReputationRequest))).one()
        assert request.status == "PENDING"
        assert request.value == 1
    await send(localized_bot, "/add_sc 42 Unauthorized payment reason")
    assert last_screen(localized_bot).text == t("p.denied", lang=lang)
    await click(localized_bot, Action(name="admin").pack())
    assert localized_bot[2].calls[-1].text == t("p.denied", lang=lang)
    await send(localized_bot, "/report 43 Lost payment incident")
    assert last_screen(localized_bot).text == t("p.evidence", lang=lang)
    bot, dispatcher, _ = localized_bot
    state = dispatcher.fsm.get_context(bot=bot, chat_id=1, user_id=1)
    nonce = (await state.get_data())["nonce"]
    await click(localized_bot, ReportStep(action="preview", nonce=nonce).pack())
    nonce = (await state.get_data())["nonce"]
    await click(localized_bot, ReportStep(action="submit", nonce=nonce).pack())
    async with database() as session:
        report = (await session.scalars(select(Report))).one()
        assert report.status == "PENDING"
    assert await state.get_state() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_localized_lookup_top_registry_and_group_admin_actions(
    localized_bot, database, settings, lang, monkeypatch
):
    from app.i18n import t
    from app.models import ScamRecord

    clock = [1000.0]
    monkeypatch.setattr("app.bot.middleware.monotonic", lambda: clock[0])
    async with database() as session:
        service = Service(settings, session)
        await service.set_language(1, lang)
        await service.set_language(900, lang)
        await service.observe(42, "target_user", "Original name")
    await send(localized_bot, "/add_sc @target_user Original scam reason", actor=900, chat=-100)
    assert "@target_user" in last_screen(localized_bot).text
    await send(localized_bot, "/ask 42", chat=-100)
    assert t("p.alert_title", lang=lang) in last_screen(localized_bot).text
    assert t("p.scam_admin_confirmation", lang=lang) in last_screen(localized_bot).text
    assert "Original scam reason" not in last_screen(localized_bot).text
    await send(localized_bot, "/rep 42")
    assert t("p.alert_title", lang=lang) in last_screen(localized_bot).text
    await send(localized_bot, "/scammers")
    assert t("p.scams_title", lang=lang) in last_screen(localized_bot).text
    await send(localized_bot, "/top")
    assert t("p.leaderboard_title", lang=lang) in last_screen(localized_bot).text
    await send(localized_bot, "/del_sc 42 Original removal reason", actor=900, chat=-100)
    async with database() as session:
        record = (await session.scalars(select(ScamRecord))).one()
        assert record.status == "REMOVED"
        assert record.removal_reason == "Original removal reason"
        assert record.reason == "Original scam reason"
    clock[0] += 10
    await send(localized_bot, "/ask 42", chat=-100)
    assert t("p.no_scam", lang=lang) in last_screen(localized_bot).text
    assert t("p.warning", lang=lang) in last_screen(localized_bot).text


@pytest.mark.asyncio
async def test_positive_negative_reputation_shared_across_language_changes(
    localized_bot, database, settings
):
    from app.i18n import t
    from app.models import ReputationRequest

    async with database() as session:
        service = Service(settings, session)
        await service.set_language(1, "en")
        await service.set_language(2, "ru")
    await send(localized_bot, "+rep 43 Test comment", actor=1)
    await send(localized_bot, "-rep 43 Test comment", actor=2)
    async with database() as session:
        requests = (
            await session.scalars(select(ReputationRequest).order_by(ReputationRequest.id))
        ).all()
        assert [item.value for item in requests] == [1, -1]
        service = Service(settings, session)
        for request in requests:
            await service.moderate_reputation(900, request.reference, True)
        before = await service.profile("43")
        assert (before["score"], before["positive"], before["negative"]) == (0, 1, 1)
    await click(localized_bot, "language:ru", actor=1)
    await send(localized_bot, "/rep 43", actor=1)
    assert t("p.profile_title", lang="ru") in last_screen(localized_bot).text
    async with database() as session:
        after = await Service(settings, session).profile("43")
        assert (after["score"], after["positive"], after["negative"]) == (0, 1, 1)


@pytest.mark.asyncio
async def test_missing_user_and_stale_callback_localized(localized_bot, database, settings):
    from app.i18n import t

    async with database() as session:
        await Service(settings, session).set_language(1, "en")
    await click(localized_bot, Action(name="vote+", value="u:999999").pack())
    assert last_screen(localized_bot).text == t("error.not_found", "en")
    await click(localized_bot, "untrusted:callback")
    assert localized_bot[2].calls[-1].text == t("p.stale", "en")

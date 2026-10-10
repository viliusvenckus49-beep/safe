from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from test_integration_contracts import postgres_contract as postgres_contract
from test_telegram import journey as telegram_journey

from app.bot import redsafe_profile as rp
from app.bot.callbacks import Action
from app.i18n import use_language
from app.models import ManagedGroup, ProfileActivity
from app.services import Service


def message(mid=1, text="Hello", **extra):
    return Message(
        message_id=mid,
        date=datetime.now(UTC),
        chat=Chat(id=-1001, type="supergroup"),
        from_user=TelegramUser(id=123, is_bot=False, first_name="Name", username="tester"),
        text=text,
        **extra,
    )


@pytest.mark.parametrize("index,threshold", enumerate(rp.THRESHOLDS))
def test_levels_require_both_thresholds(index, threshold):
    assert rp.progression(*threshold) == (index, 100 if index == 9 else 0)
    if index:
        assert rp.progression(threshold[0] - 1, threshold[1])[0] < index
        assert rp.progression(threshold[0], threshold[1] - 1)[0] < index


def test_exact_design_escape_and_callbacks():
    user = SimpleNamespace(
        id=7, telegram_id=28563234, username="juodojimaterija", display_name="Name"
    )
    with use_language("lt"):
        text = rp.profile_text(dict(user=user, messages=1, days=1, groups=1, network_days=8))
        assert text == (
            rp.BRAND + "\n\n🪪 <b>REDSAFE PROFILIS</b>\n━━━━━━━━━━━━━━\n\n"
            "👤 @juodojimaterija\n🏷 01 · <b>NAUJOKAS</b>\n🆔 ID: 28563234\n\n"
            "<blockquote>📊 <b>Veiklos statistika</b>\n\n💬 Žinutės: 1\n🔥 Aktyvios dienos: 1\n"
            "🌐 REDSAFE grupės: 1\n📅 REDSAFE tinkle: 8 d.</blockquote>\n\n"
            "━━━━━━━━━━━━━━\n◈ <b>PROFILIO PROGRESAS</b>\n\n🏅 Kitas statusas: <b>VIETINIS</b>\n\n"
            "▰▰▱▱▱▱▱▱▱▱ 20%"
        )
        buttons = rp.controls(7).inline_keyboard
        assert [row[0].text for row in buttons] == ["👁️‍🗨️ Naudotojo vardai", "‹ Pagrindinis meniu"]
        assert Action.unpack(buttons[0][0].callback_data).value == "u:7"
        assert Action.unpack(buttons[1][0].callback_data).name == "home"
        user.username = None
        user.display_name = "<script>&"
        assert "&lt;script&gt;&amp;" in rp.profile_text(
            dict(user=user, messages=0, days=0, groups=0, network_days=0)
        )


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_localized_profile(lang):
    with use_language(lang):
        text = rp.profile_text(
            dict(
                user=SimpleNamespace(id=1, telegram_id=123, username="test", display_name="test"),
                messages=6000,
                days=365,
                groups=2,
                network_days=400,
            )
        )
        assert text.count("<blockquote>") == text.count("</blockquote>") == 1
        assert "▰▰▰▰▰▰▰▰▰▰ 100%" in text
        assert "redsafe." not in text
        assert rp.controls(1).inline_keyboard[1][0].text.startswith("‹ ")


@pytest.mark.asyncio
async def test_deduplication_restart_dates_exclusions_and_membership(database, settings):
    async with database() as session:
        session.add(ManagedGroup(chat_id=-1001, title="One", approved=True, enabled=True))
        session.add(ManagedGroup(chat_id=-1002, title="Two", approved=True, enabled=True))
        session.add(ManagedGroup(chat_id=-1003, title="Inactive", approved=True, enabled=False))
        await session.commit()
        service = Service(settings, session)
        await rp.record_activity(message(), service)
        await rp.record_activity(message(), service)
        await rp.record_activity(message(2, "/ask @tester"), service)
        await rp.record_activity(message(3, "-rep @tester reason"), service)
        await rp.record_activity(
            message(
                4, None, new_chat_members=[TelegramUser(id=222, is_bot=False, first_name="Other")]
            ),
            service,
        )
        await session.commit()
    # Fresh database session simulates a bot restart and a duplicate delivered again.
    async with database() as session:
        service = Service(settings, session)
        await rp.record_activity(message(), service)
        earlier = message(5).model_copy(update={"date": datetime.now(UTC) - timedelta(days=8)})
        await rp.record_activity(earlier, service)
        await session.commit()
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(
                side_effect=[SimpleNamespace(status="member"), SimpleNamespace(status="left")]
            )
        )
        data = await rp.profile_data(service, bot, "123")
        assert (data["messages"], data["days"], data["groups"], data["network_days"]) == (
            2,
            2,
            1,
            8,
        )
        assert bot.get_chat_member.await_count == 2
        assert await session.scalar(select(func.count()).select_from(ProfileActivity)) == 2
        user = await service.repo.user_by_telegram(123)
        old_id = user.id
        await service.observe(123, "renamed", "Name")
        assert (await service.repo.user_by_telegram(123)).id == old_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ProfileActivity)
                .where(ProfileActivity.user_id == old_id)
            )
            == 2
        )
        history = await rp.names_text(service, "123")
        assert "@tester" in history and "@renamed" in history


@pytest.mark.asyncio
async def test_no_invented_activity_or_membership(database, settings):
    async with database() as session:
        service = Service(settings, session)
        user = await service.observe(123, None, "No username")
        user.created_at = datetime.now(UTC) - timedelta(days=100)
        session.add(ManagedGroup(chat_id=-1001, title="Group", approved=True, enabled=True))
        await session.commit()
        bot = SimpleNamespace(get_chat_member=AsyncMock(side_effect=TimeoutError))
        data = await rp.profile_data(service, bot, "123")
        assert [data[key] for key in ("messages", "days", "groups", "network_days")] == [0, 0, 0, 0]
        assert "istorijos dar nėra" in await rp.names_text(service, "123")


journey = telegram_journey


@pytest.mark.asyncio
async def test_info_and_history_routes_and_existing_home(journey):
    await journey.send("/info 42")
    screen = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage)
    )
    assert "REDSAFE PROFILIS" in screen.text
    history_callback = screen.reply_markup.inline_keyboard[0][0].callback_data
    await journey.click(history_callback)
    screen = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage)
    )
    assert "Naudotojo vardų istorijos dar nėra." in screen.text
    await journey.click(screen.reply_markup.inline_keyboard[0][0].callback_data)
    assert "Pasirink veiksmą" in journey.text()


@pytest.mark.asyncio
async def test_real_group_messages_are_observed_without_commands(journey, database):
    async with database() as session:
        session.add(ManagedGroup(chat_id=-100, title="Red", approved=True, enabled=True))
        await session.commit()
    await journey.send("ordinary message", chat=-100)
    # Only the group message is tracked; commands and private messages do not count.
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ProfileActivity)) == 1
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(ProfileActivity)) == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_delivery_is_counted_once(postgres_contract):
    import asyncio

    sessions, settings = postgres_contract
    async with sessions() as session:
        session.add(ManagedGroup(chat_id=-1001, title="Red", approved=True, enabled=True))
        await session.commit()
        await Service(settings, session).observe(123, "tester", "Name")

    async def deliver():
        async with sessions() as session:
            await rp.record_activity(message(), Service(settings, session))
            await session.commit()

    await asyncio.gather(deliver(), deliver(), deliver())
    async with sessions() as session:
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member"))
        )
        data = await rp.profile_data(Service(settings, session), bot, "123")
        assert (data["messages"], data["days"], data["groups"]) == (1, 1, 1)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_moderator_status_is_bold_and_does_not_change_progression(lang):
    with use_language(lang):
        from app.i18n import t

        data = dict(
            user=SimpleNamespace(telegram_id=42, username="mod", display_name="Mod"),
            messages=1,
            days=1,
            groups=1,
            network_days=8,
            moderator=True,
        )
        text = rp.profile_text(data)
        assert f"🏷 01 · <b>{t('redsafe.moderator')}</b>" in text
        assert f"<b>{t('redsafe.levels').split('|')[1]}</b>" in text
        assert "20%" in text


@pytest.mark.asyncio
async def test_profile_moderator_uses_live_access_and_revocation(database, settings):
    async with database() as session:
        service = Service(settings, session)
        bot = SimpleNamespace(get_chat_member=AsyncMock())
        await service.access.change(900, "42", True, "a" * 32)
        data = await rp.profile_data(service, bot, "42")
        assert data["moderator"] is True
        await service.access.change(900, "42", False, "b" * 32)
        assert (await rp.profile_data(service, bot, "42"))["moderator"] is False
        assert (await rp.profile_data(service, bot, "900"))["moderator"] is False

"""Persistent access control, owner guards, retries and actual Telegram journeys."""

from datetime import UTC, datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import Chat, Message, User
from sqlalchemy import func, select
from test_telegram import Journey, Transport

from app.admin_services import AdminService
from app.bot.callbacks import Action, AdminAccess, AdminHelp
from app.bot.handlers import create_router
from app.bot.states import AdminAccessFlow
from app.errors import DomainError
from app.i18n import CATALOGS
from app.models import Administrator, AdministratorChange, AuditEvent
from app.services import Service

pytestmark = pytest.mark.asyncio


async def test_dynamic_grant_revoke_regrant_and_stale_retry(database, settings):
    async with database() as session:
        access = AdminService(settings, session)
        assert not await access.is_admin(42)
        assert await access.change(900, "42", True, "a" * 32)
        assert await access.is_admin(42)
        assert not await access.change(900, "42", True, "a" * 32)
        assert await access.change(900, "42", False, "b" * 32)
        assert not await access.is_admin(42)
        assert not await access.change(900, "42", True, "a" * 32)
        assert not await access.is_admin(42)  # Old delivery cannot regrant access.
        assert await access.change(900, "42", True, "c" * 32)
        assert await session.scalar(select(func.count()).select_from(AdministratorChange)) == 3
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 3
    async with database() as session:
        assert await AdminService(settings, session).is_admin(42)
        await Service(settings, session).add_scam(42, "43", "Documented reason for scam")


async def test_configured_admin_revocation_persists(database, settings):
    settings.admin_ids = "900,901"
    settings.group_owner_id = 900
    async with database() as session:
        access = AdminService(settings, session)
        assert await access.is_admin(901)
        await access.change(900, "901", False, "a" * 32)
    async with database() as session:
        access = AdminService(settings, session)
        assert not await access.is_admin(901)
        with pytest.raises(DomainError, match="forbidden"):
            await Service(settings, session).add_scam(901, "42", "Documented reason for scam")


@pytest.mark.parametrize("actor", [1, 42, 901])
async def test_admin_cannot_delegate_or_manage_groups(database, settings, actor):
    settings.admin_ids = "900,901"
    settings.group_owner_id = 900
    async with database() as session:
        access = AdminService(settings, session)
        await access.change(900, "42", True, "a" * 32)
        for call in (
            access.change(actor, "43", True, "b" * 32),
            access.admins(actor),
            access.identity(actor, "43"),
        ):
            with pytest.raises(DomainError, match="forbidden"):
                await call
        from app.group_services import GroupService

        with pytest.raises(DomainError, match="forbidden"):
            await GroupService(settings, session).groups(actor)


@pytest.mark.parametrize(
    "target", ["@example", "0", "-1", "1e3", "9223372036854775808", "1 2", "<b>42</b>"]
)
async def test_invalid_admin_identity(database, settings, target):
    async with database() as session:
        with pytest.raises(DomainError, match="admin_id_invalid"):
            await AdminService(settings, session).change(900, target, True, "a" * 32)
        assert await session.scalar(select(func.count()).select_from(Administrator)) == 0


@pytest.mark.parametrize("active", [True, False])
async def test_owner_is_immutable(database, settings, active):
    async with database() as session:
        access = AdminService(settings, session)
        with pytest.raises(DomainError, match="admin_owner_immutable"):
            await access.change(900, "900", active, "a" * 32)
        assert await access.is_admin(900)


async def test_unknown_owner_fails_closed(database, settings):
    settings.admin_ids = "900,901"
    async with database() as session:
        with pytest.raises(DomainError, match="forbidden"):
            await AdminService(settings, session).change(900, "42", True, "a" * 32)


async def test_request_key_cannot_be_reused_for_another_action(database, settings):
    async with database() as session:
        access = AdminService(settings, session)
        await access.change(900, "42", True, "a" * 32)
        with pytest.raises(DomainError, match="stale_callback"):
            await access.change(900, "43", True, "a" * 32)
        assert not await access.is_admin(43)


async def make_journey(database, settings):
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(create_router(settings, database))
    return Journey(bot, dp, transport)


async def finish(journey):
    await journey.dp.storage.close()
    await journey.bot.session.close()


async def test_owner_add_remove_wizard_and_old_callbacks(database, settings):
    j = await make_journey(database, settings)
    try:
        await j.send("/admins", actor=900)
        assert CATALOGS["lt"]["admin_access.title"] in j.text()
        await j.click(AdminAccess(action="add").pack(), actor=900)
        assert await j.state(900) == AdminAccessFlow.target.state
        await j.send("42", actor=900)
        assert await j.state(900) == AdminAccessFlow.preview.state
        async with database() as session:
            assert not await AdminService(settings, session).is_admin(42)
        nonce = (await j.data(900))["access_nonce"]
        submit = AdminAccess(action="submit", value=nonce).pack()
        await j.click(submit, actor=900)
        await j.click(submit, actor=900)
        async with database() as session:
            assert await AdminService(settings, session).is_admin(42)
            assert await session.scalar(select(func.count()).select_from(AdministratorChange)) == 1
        await j.send("/admin", actor=42)
        assert any(
            isinstance(call, SendMessage) and call.reply_markup for call in j.transport.calls
        )
        await j.click(AdminAccess(action="remove", value="42").pack(), actor=900)
        nonce = (await j.data(900))["access_nonce"]
        await j.click(AdminAccess(action="submit", value=nonce).pack(), actor=900)
        async with database() as session:
            assert not await AdminService(settings, session).is_admin(42)
        await j.click(Action(name="stats").pack(), actor=42)
        assert any(
            isinstance(call, AnswerCallbackQuery) and call.show_alert for call in j.transport.calls
        )
        await j.send("/admin_help", actor=42)
        assert CATALOGS["lt"]["p.denied"] in j.text()
    finally:
        await finish(j)


async def test_admin_confirmation_cancel_and_nonce_tampering(database, settings):
    j = await make_journey(database, settings)
    try:
        await j.send("/admins 42", actor=900)
        await j.click(AdminAccess(action="submit", value="f" * 32).pack(), actor=900)
        async with database() as session:
            assert not await AdminService(settings, session).is_admin(42)
        await j.click(Action(name="close").pack(), actor=900)
        assert await j.state(900) is None
        async with database() as session:
            assert await session.scalar(select(func.count()).select_from(AdministratorChange)) == 0
    finally:
        await finish(j)


async def test_reply_identity_escapes_name(database, settings):
    j = await make_journey(database, settings)
    try:
        replied = Message(
            message_id=7,
            date=datetime.now(UTC),
            chat=Chat(id=900, type="private"),
            from_user=User(id=42, is_bot=False, first_name="<Bad> & name"),
            text="Hi",
        )
        await j.send("/admins", actor=900, reply_to_message=replied)
        assert "&lt;Bad&gt; &amp; name" in j.text()
        assert (await j.data(900))["access_target"] == 42
    finally:
        await finish(j)


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
async def test_handbook_every_section_and_dynamic_admin_menu(database, settings, locale):
    async with database() as session:
        service = Service(settings, session)
        await service.access.change(900, "42", True, "a" * 32)
        await service.set_language(42, locale)
    j = await make_journey(database, settings)
    try:
        await j.send("/admin", actor=42)
        markup = j.transport.calls[-1].reply_markup
        callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
        assert AdminHelp().pack() in callbacks
        assert AdminAccess(action="list").pack() not in callbacks
        await j.send("/admin_help", actor=42)
        assert CATALOGS[locale]["admin_help.menu"] in j.text()
        from app.bot.keyboards import HELP_SECTIONS

        for section in HELP_SECTIONS:
            await j.click(AdminHelp(section=section).pack(), actor=42)
            assert CATALOGS[locale]["admin_help." + section] in j.text()
        await j.click(AdminHelp(section="tampered").pack(), actor=42)
        assert CATALOGS[locale]["p.stale"] in j.text()
    finally:
        await finish(j)


@pytest.mark.parametrize("action", ["list", "add", "remove", "submit"])
async def test_callback_permission_bypass(database, settings, action):
    j = await make_journey(database, settings)
    try:
        await j.click(
            AdminAccess(action=action, value="42" if action == "remove" else "").pack(), actor=1
        )
        async with database() as session:
            assert await session.scalar(select(func.count()).select_from(Administrator)) == 0
        assert CATALOGS["lt"]["p.denied"] in j.text()
    finally:
        await finish(j)


async def test_noop_request_is_consumed_before_opposite_decision(database, settings):
    settings.admin_ids = "900,42"
    settings.group_owner_id = 900
    async with database() as session:
        access = AdminService(settings, session)
        assert not await access.change(900, "42", True, "a" * 32)
        assert await access.change(900, "42", False, "b" * 32)
        assert not await access.change(900, "42", True, "a" * 32)
        assert not await access.is_admin(42)
        assert await session.scalar(select(func.count()).select_from(AdministratorChange)) == 2
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1


async def test_owner_menu_and_management_private_guard(database, settings):
    j = await make_journey(database, settings)
    try:
        await j.send("/admin", actor=900)
        markup = j.transport.calls[-1].reply_markup
        assert any(
            button.callback_data == AdminAccess(action="list").pack()
            for row in markup.inline_keyboard
            for button in row
        )
        await j.click(AdminAccess(action="add").pack(), actor=900, chat=-1001)
        assert CATALOGS["lt"]["p.admin_private"] in j.text()
        async with database() as session:
            assert await session.scalar(select(func.count()).select_from(Administrator)) == 0
    finally:
        await finish(j)


async def test_administrator_pagination_and_constraints(database, settings):
    from sqlalchemy.exc import IntegrityError

    async with database() as session:
        access = AdminService(settings, session)
        for number in range(42, 51):
            await access.change(900, str(number), True, f"{number:032x}")
        first, total = await access.admins(900, 0)
        second, _ = await access.admins(900, 1)
        last, _ = await access.admins(900, 999)
        assert total == 10 and len(first) == 8 and len(second) == 2
        assert {item[0] for item in first}.isdisjoint(item[0] for item in second)
        assert [item[0] for item in last] == [item[0] for item in second]
        session.add(Administrator(telegram_id=-1, active=True, assigned_by=900))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

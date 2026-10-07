"""Thin owner access-management flows and administrator handbook routing."""

import re
from uuid import uuid4

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import admin_presentation as p
from app.bot import keyboards as kb
from app.bot.callbacks import AdminAccess, AdminHelp
from app.bot.screens import clear_flow, flow_screen, render
from app.bot.states import AdminAccessFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.i18n import t
from app.services import Service


def require_private(message: Message) -> None:
    if message.chat.type != "private":
        raise DomainError("admin_private")


async def show_list(
    message: Message, state: FSMContext, service: Service, actor: int, page: int = 0
) -> None:
    rows, total = await service.access.admins(actor, page)
    page = min(page, max(0, (total - 1) // service.access.PAGE_SIZE))
    await clear_flow(state)
    await flow_screen(
        message,
        state,
        p.listing(rows, service.settings.group_owner),
        reply_markup=kb.administrator_menu(rows, page, total, service.settings.group_owner),
    )


async def show_preview(
    message: Message, state: FSMContext, service: Service, actor: int, target: str, active: bool
) -> None:
    tg_id, user = await service.access.identity(actor, target)
    nonce = uuid4().hex
    await clear_flow(state)
    await state.set_state(AdminAccessFlow.preview)
    await state.update_data(
        access_actor=actor, access_target=tg_id, access_active=active, access_nonce=nonce
    )
    await flow_screen(
        message, state, p.preview(tg_id, user, active), reply_markup=kb.administrator_preview(nonce)
    )


def register_administrator_handlers(router: Router) -> None:
    @router.message(Command("admins"))
    async def admins_command(message: Message, state: FSMContext, service: Service) -> None:
        actor = actor_id(message)
        service.access.require_owner(actor)
        require_private(message)
        replied = message.reply_to_message.from_user if message.reply_to_message else None
        arguments = (message.text or "").split(maxsplit=1)
        if len(arguments) == 2:
            await show_preview(message, state, service, actor, arguments[1], True)
        elif replied and not replied.is_bot:
            await service.observe(replied.id, replied.username, replied.full_name)
            await show_preview(message, state, service, actor, str(replied.id), True)
        else:
            await show_list(message, state, service, actor)

    @router.message(Command("admin_help"))
    async def help_command(message: Message, state: FSMContext, service: Service) -> None:
        await service.require_admin(actor_id(message))
        require_private(message)
        await clear_flow(state)
        await flow_screen(
            message, state, t("admin_help.menu"), reply_markup=kb.administrator_help()
        )

    @router.callback_query(AdminHelp.filter())
    async def help_callback(
        query: CallbackQuery, callback_data: AdminHelp, state: FSMContext, service: Service
    ) -> None:
        await service.require_admin(query.from_user.id)
        if not isinstance(query.message, Message):
            raise DomainError("stale_callback")
        require_private(query.message)
        section = callback_data.section
        if section != "menu" and section not in kb.HELP_SECTIONS:
            raise DomainError("stale_callback")
        await query.answer()
        await clear_flow(state)
        await render(
            query.message, t("admin_help." + section), reply_markup=kb.administrator_help(section)
        )

    @router.callback_query(AdminAccess.filter())
    async def access_callback(
        query: CallbackQuery, callback_data: AdminAccess, state: FSMContext, service: Service
    ) -> None:
        actor = query.from_user.id
        service.access.require_owner(actor)
        if not isinstance(query.message, Message):
            raise DomainError("stale_callback")
        require_private(query.message)
        operation, value = callback_data.action, callback_data.value
        if operation == "list":
            if value and not re.fullmatch(r"[0-9]{1,6}", value):
                raise DomainError("stale_callback")
            await query.answer()
            await state.update_data(screen_message_id=query.message.message_id)
            await show_list(query.message, state, service, actor, int(value or "0"))
        elif operation == "add" and not value:
            await query.answer()
            await clear_flow(state)
            await state.set_state(AdminAccessFlow.target)
            await render(
                query.message, t("admin_access.target"), reply_markup=kb.administrator_input()
            )
        elif operation == "remove":
            await query.answer()
            await state.update_data(screen_message_id=query.message.message_id)
            await show_preview(query.message, state, service, actor, value, False)
        elif operation == "submit":
            draft = await state.get_data()
            if (
                await state.get_state() != AdminAccessFlow.preview.state
                or not re.fullmatch(r"[a-f0-9]{32}", value)
                or draft.get("access_nonce") != value
                or draft.get("access_actor") != actor
                or type(draft.get("access_active")) is not bool
                or type(draft.get("access_target")) is not int
            ):
                raise DomainError("stale_callback")
            tg_id, user = await service.access.identity(actor, str(draft["access_target"]))
            changed = await service.access.change(actor, str(tg_id), draft["access_active"], value)
            await query.answer()
            await clear_flow(state)
            key = "added" if draft["access_active"] else "removed"
            await render(
                query.message,
                t(
                    "admin_access." + (key if changed else "unchanged"),
                    identity=p.identity(tg_id, user),
                ),
                reply_markup=kb.administrator_input(completed=True),
            )
        else:
            raise DomainError("stale_callback")

    @router.message(AdminAccessFlow.target)
    async def access_target(message: Message, state: FSMContext, service: Service) -> None:
        actor = actor_id(message)
        service.access.require_owner(actor)
        require_private(message)
        replied = message.reply_to_message.from_user if message.reply_to_message else None
        if replied and not replied.is_bot:
            await service.observe(replied.id, replied.username, replied.full_name)
            target = str(replied.id)
        else:
            target = message.text or ""
        await show_preview(message, state, service, actor, target, True)

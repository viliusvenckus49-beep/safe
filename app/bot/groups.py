"""Group registration, live moderation and retained group history."""

import asyncio
import secrets
from html import escape
from typing import Any

import structlog
from aiogram import Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from app.bot import group_keyboards as kb
from app.bot import group_presentation as p
from app.bot.group_authorization import verify_group
from app.bot.group_runtime import process_scam_bans
from app.bot.screens import clear_flow, flow_screen, render
from app.bot.states import GroupRemovalFlow
from app.config import Settings
from app.group_services import GroupService
from app.i18n import t
from app.services import DomainError


async def _render(event: CallbackQuery, text: str, markup: Any) -> None:
    await event.answer()
    if isinstance(event.message, Message):
        await render(event.message, text, reply_markup=markup)


def register_group_handlers(router: Router, settings: Settings, sessions: Any) -> None:
    @router.my_chat_member()
    async def bot_membership(event: ChatMemberUpdated) -> None:
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return
        member = event.new_chat_member
        allowed = member.status == ChatMemberStatus.ADMINISTRATOR and bool(
            getattr(member, "can_restrict_members", False)
        )
        async with sessions() as session:
            service = GroupService(settings, session)
            if member.status in {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}:
                await service.disable_group(event.chat.id)
            elif not allowed:
                await service.note_group_permissions(event.chat.id, False)
            elif event.from_user.id == settings.group_owner:
                await service.stage_group(
                    event.from_user.id,
                    event.chat.id,
                    event.chat.title or "SAFECheck",
                    chat_type=str(event.chat.type),
                )
                await service.note_group_permissions(event.chat.id, True)
            else:
                await service.note_group_permissions(event.chat.id, True)

    @router.chat_member()
    async def member_update(event: ChatMemberUpdated) -> None:
        if event.chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return
        user = event.new_chat_member.user
        if user.is_bot:
            return
        if event.new_chat_member.status not in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.RESTRICTED,
        }:
            return
        async with sessions() as session:
            service = GroupService(settings, session)
            if await service.is_removed_group(event.chat.id):
                return
            await service.observe_member(event.chat.id, user.id, user.username, user.full_name)
            await service.check_member(event.chat.id, user.id, fresh_join=True)
            known = await service.core.repo.user_by_telegram(user.id)
            record = await service.core.repo.active_scam(known.id) if known is not None else None
            record_id = record.id if record is not None else None
            await session.commit()
        if event.bot is not None:
            from app.bot.checks_runtime import inspect_join

            try:
                await inspect_join(event, sessions)
            except Exception as error:
                structlog.get_logger().warning(
                    "checks_join_deferred", exception_type=type(error).__name__
                )
        if record_id is not None and event.bot is not None:
            try:
                await process_scam_bans(event.bot, settings, sessions, record_id)
            except Exception as error:
                structlog.get_logger().warning(
                    "scam_ban_processing_deferred",
                    operation="ban_chat_member",
                    chat_id=event.chat.id,
                    user_id=user.id,
                    scam_record_id=record_id,
                    exception_type=type(error).__name__,
                )

    @router.message(Command("groups"))
    async def groups_command(message: Message, state: FSMContext) -> None:
        if message.chat.type != ChatType.PRIVATE:
            return
        assert message.from_user is not None
        await clear_flow(state)
        async with sessions() as session:
            groups = await GroupService(settings, session).groups(message.from_user.id)
        await flow_screen(
            message,
            state,
            p.text("TITLE") if groups else p.text("EMPTY"),
            reply_markup=kb.menu(groups, admin=True),
        )

    @router.callback_query(kb.GroupAction.filter())
    async def group_action(
        query: CallbackQuery, callback_data: kb.GroupAction, state: FSMContext
    ) -> None:
        if not isinstance(query.message, Message) or (
            query.message.chat.type != ChatType.PRIVATE and callback_data.action != "approve"
        ):
            await query.answer(p.text("PRIVATE_ONLY"), show_alert=True)
            return
        actor = query.from_user.id
        action = callback_data.action
        if action not in {"list", "group", "approve", "remove", "remove_confirm"}:
            await clear_flow(state)
            await query.answer(t("nav.removed"), show_alert=True)
            return
        async with sessions() as session:
            service = GroupService(settings, session)
            service.require_owner(actor)
            if action == "approve":
                if (
                    query.message.chat.type != ChatType.PRIVATE
                    and query.message.chat.id != callback_data.chat_id
                ):
                    raise DomainError("forbidden")
                available = await service.groups(actor)
                candidate = next((g for g in available if g.chat_id == callback_data.chat_id), None)
                if candidate is None:
                    raise DomainError("stale_callback")
                assert query.bot is not None
                chat = await verify_group(query.bot, settings, actor, candidate.chat_id)
                approved_group = await service.register_group(
                    actor, chat.id, chat.title or "SAFECheck", True, chat_type=str(chat.type)
                )
                await _render(
                    query,
                    p.text("APPROVED")
                    + "\n\n"
                    + p.group_card(
                        approved_group.title,
                        approved_group.enabled,
                        approved_group.chat_type,
                        approved=True,
                    ),
                    kb.group(approved_group.chat_id)
                    if query.message.chat.type == ChatType.PRIVATE
                    else kb.enrollment(approved_group.chat_id, approved=True),
                )
                return
            available = await service.groups(actor)
            chosen = next((g for g in available if g.chat_id == callback_data.chat_id), None)
            if action == "list":
                await clear_flow(state)
                await _render(
                    query,
                    p.text("TITLE") if available else p.text("EMPTY"),
                    kb.menu(available, admin=True),
                )
                return
            if chosen is None:
                raise DomainError("stale_callback")
            if action == "group":
                await clear_flow(state)
                await _render(
                    query,
                    p.group_card(
                        chosen.title, chosen.enabled, chosen.chat_type, approved=chosen.approved
                    ),
                    kb.group(chosen.chat_id, approved=chosen.approved),
                )
            elif action == "remove":
                await clear_flow(state)
                nonce = secrets.token_hex(8)
                await state.set_state(GroupRemovalFlow.confirm)
                await state.set_data({"group_chat_id": chosen.chat_id, "nonce": nonce})
                await _render(
                    query,
                    t("group.REMOVE_CONFIRM", title=escape(chosen.title)),
                    kb.remove_confirmation(chosen.chat_id, nonce),
                )
            elif action == "remove_confirm":
                draft = await state.get_data()
                if (
                    await state.get_state() != GroupRemovalFlow.confirm.state
                    or draft.get("group_chat_id") != chosen.chat_id
                    or not callback_data.nonce
                    or draft.get("nonce") != callback_data.nonce
                ):
                    raise DomainError("stale_callback")
                await service.remove_group(actor, chosen.chat_id)
                await clear_flow(state)
                left = False
                try:
                    assert query.bot is not None
                    left = await asyncio.wait_for(query.bot.leave_chat(chosen.chat_id), 10) is True
                except (TelegramAPIError, TimeoutError, OSError) as error:
                    structlog.get_logger().warning(
                        "group_leave_failed",
                        operation="leave_chat",
                        chat_id=chosen.chat_id,
                        exception_type=type(error).__name__,
                    )
                groups = await service.groups(actor)
                await _render(
                    query,
                    t("group.REMOVED", title=escape(chosen.title))
                    + ("" if left else "\n\n" + p.text("LEAVE_FAILED")),
                    kb.menu(groups, admin=True),
                )
            else:
                raise DomainError("stale_callback")

from collections.abc import Awaitable, Callable
from datetime import UTC
from time import monotonic
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy import select
from structlog.contextvars import bind_contextvars, reset_contextvars

from app import presentation as p
from app.bot.group_runtime import process_scam_bans
from app.bot.navigation import error_navigation
from app.bot.screens import flow_screen, panel_state, preserved_source, render
from app.group_services import GroupService
from app.i18n import use_language
from app.models import AuditEvent
from app.repositories import Repository
from app.services import DomainError, Service


class ServiceMiddleware(BaseMiddleware):
    def __init__(self, settings: Any, session_factory: Any) -> None:
        self.recent: dict[int, list[float]] = {}
        self.settings = settings
        self.session_factory = session_factory

    async def __call__(
        self, handler: Callable[..., Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        actor = getattr(event, "from_user", None)
        async with self.session_factory() as session:
            selected = (
                await Repository(session).language(actor.id) if actor and not actor.is_bot else None
            )
        with use_language(selected):
            return await self._localized(handler, event, data)

    async def _localized(
        self, handler: Callable[..., Awaitable[Any]], event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        actor = getattr(event, "from_user", None)
        group_message = isinstance(event, Message) and event.chat.type in {"group", "supergroup"}
        actionable = not group_message or (
            isinstance(event, Message)
            and bool(
                event.text
                and (
                    event.text.startswith("/")
                    or event.text.lower().split(maxsplit=1)[:1] in [["+rep"], ["-rep"]]
                )
            )
        )
        if not actor or actor.is_bot or (isinstance(event, Message) and event.sender_chat):
            if isinstance(event, CallbackQuery):
                await event.answer(p.error("anonymous_identity"), show_alert=True)
            elif isinstance(event, Message) and actionable:
                await event.answer(p.error("anonymous_identity"))
            return None
        message = event.message if isinstance(event, CallbackQuery) else event
        chat = getattr(message, "chat", None)
        context = bind_contextvars(
            user_id=actor.id,
            chat_id=getattr(chat, "id", None),
            operation="callback" if isinstance(event, CallbackQuery) else "message",
        )
        screen_context = panel_state.set(data.get("state"))
        preservation_context = preserved_source.set(None)
        log = structlog.get_logger()
        try:
            # Observe and enforce even updates the UI intentionally ignores. Unknown
            # groups are ignored by GroupService; no Telegram lookup is performed.
            if group_message and isinstance(event, Message):
                async with self.session_factory() as group_session:
                    groups = GroupService(self.settings, group_session)
                    if (
                        actor.id == self.settings.group_owner
                        and (event.text or "").split("@", 1)[0].split(maxsplit=1)[0:1] == ["/start"]
                        and event.bot is not None
                    ):
                        try:
                            member = await event.bot.get_chat_member(event.chat.id, event.bot.id)
                            if member.status == "administrator" and getattr(
                                member, "can_restrict_members", False
                            ):
                                await groups.stage_group(
                                    actor.id,
                                    event.chat.id,
                                    event.chat.title or "SAFECheck",
                                    chat_type=str(event.chat.type),
                                )
                            else:
                                await groups.note_group_permissions(event.chat.id, False)
                        except TelegramAPIError as error:
                            log.warning(
                                "group_registration_failed", exception_type=type(error).__name__
                            )
                    await groups.observe_member(
                        event.chat.id, actor.id, actor.username, actor.full_name
                    )
                    await groups.check_member(event.chat.id, actor.id)
                    if isinstance(event, Message):
                        for joined in event.new_chat_members or []:
                            if not joined.is_bot:
                                await groups.observe_member(
                                    event.chat.id, joined.id, joined.username, joined.full_name
                                )
                                await groups.check_member(event.chat.id, joined.id, fresh_join=True)
                    ids = {actor.id} | {
                        user.id for user in event.new_chat_members or [] if not user.is_bot
                    }
                    records = []
                    for user_id in ids:
                        known = await groups.core.repo.user_by_telegram(user_id)
                        record = await groups.core.repo.active_scam(known.id) if known else None
                        if record is not None:
                            records.append(record.id)
                    # End metadata reads before the executor performs Telegram calls.
                    await group_session.commit()
                if event.bot is not None:
                    for record_id in records:
                        try:
                            await process_scam_bans(
                                event.bot, self.settings, self.session_factory, record_id
                            )
                        except Exception as error:
                            log.warning(
                                "scam_ban_processing_deferred",
                                scam_record_id=record_id,
                                exception_type=type(error).__name__,
                            )
            current = monotonic()
            if len(self.recent) > 10000:
                self.recent = {
                    key: seen
                    for key, seen in self.recent.items()
                    if seen and current - seen[-1] < 60
                }
            recent = [seen for seen in self.recent.get(actor.id, []) if current - seen < 10]
            if actionable and len(recent) >= 20:
                if isinstance(event, CallbackQuery):
                    await event.answer(p.text("cooldown"), show_alert=True)
                elif isinstance(event, Message) and actionable:
                    await event.answer(p.text("cooldown"))
                log.info("update_rate_limited")
                return None
            if actionable:
                self.recent[actor.id] = [*recent, current]
            async with self.session_factory() as session:
                service = Service(self.settings, session)
                data["service"] = service
                data["settings"] = self.settings
                data["session_factory"] = self.session_factory
                try:
                    if isinstance(event, CallbackQuery) and isinstance(event.message, Message):
                        restored_at = await session.scalar(
                            select(AuditEvent.created_at)
                            .where(AuditEvent.action == "legacy_database_import")
                            .order_by(AuditEvent.created_at.desc())
                            .limit(1)
                        )
                        if restored_at is not None:
                            restored_at = (
                                restored_at
                                if restored_at.tzinfo
                                else restored_at.replace(tzinfo=UTC)
                            )
                            if int(event.message.date.timestamp()) < int(restored_at.timestamp()):
                                if data.get("state") is not None:
                                    await data["state"].clear()
                                await event.answer(p.text("data_refreshed"), show_alert=True)
                                log.info("callback_rejected_after_data_restore")
                                return None
                    observed = await service.observe(actor.id, actor.username, actor.full_name)
                    if not group_message and event.bot is not None:
                        record = await service.repo.active_scam(observed.id)
                        await session.commit()
                        if record is not None:
                            try:
                                await process_scam_bans(
                                    event.bot, self.settings, self.session_factory, record.id
                                )
                            except Exception as error:
                                log.warning(
                                    "scam_ban_processing_deferred",
                                    scam_record_id=record.id,
                                    exception_type=type(error).__name__,
                                )
                    if getattr(chat, "type", None) == "private":
                        await GroupService(self.settings, session).mark_private_contact(actor.id)
                    result = await handler(event, data)
                    log.info("update_handled")
                    return result
                except DomainError as exc:
                    await session.rollback()
                    log.info("operation_rejected", error_code=exc.code)
                    if exc.code == "forbidden":
                        # Permission notices remain in chat and do not replace a draft.
                        if isinstance(event, CallbackQuery):
                            if isinstance(event.message, Message):
                                await event.message.answer(p.error(exc.code))
                            else:
                                await event.answer(p.error(exc.code), show_alert=True)
                        elif isinstance(event, Message):
                            await event.answer(p.error(exc.code))
                        return None
                    # A callback may already be acknowledged; send an understandable
                    # message as well so errors never disappear after a double click.
                    markup = await error_navigation(
                        data.get("state"), private=getattr(chat, "type", None) == "private"
                    )
                    if isinstance(event, CallbackQuery):
                        if isinstance(event.message, Message):
                            await render(event.message, p.error(exc.code), reply_markup=markup)
                        else:
                            await event.answer(p.error(exc.code), show_alert=True)
                    elif isinstance(event, Message):
                        if data.get("state") is not None:
                            await flow_screen(
                                event,
                                data["state"],
                                p.error(exc.code),
                                reply_markup=markup,
                            )
                        else:
                            await event.answer(p.error(exc.code), reply_markup=markup)
                    return None
        finally:
            preserved_source.reset(preservation_context)
            panel_state.reset(screen_context)
            reset_contextvars(**context)

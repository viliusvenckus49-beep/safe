"""Public receipt controls with live authorization and durable, scoped state."""

import hashlib
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import CallbackQuery, EphemeralMessageParameters, InlineKeyboardMarkup, Message
from sqlalchemy import or_, select, update
from sqlalchemy.orm import aliased

from app.bot.callbacks import ModerationControl
from app.bot.keyboards import keyboard
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.i18n import t, use_language
from app.models import (
    GroupModerationDraft,
    GroupSanction,
    GroupWarningEscalation,
    GroupWarningReset,
    now,
)
from app.moderation_services import GroupModerationService, utc
from app.services import Service
from app.warning_services import reset_prompt, reset_warnings


def control(action: str, sanction: int, token: str = "") -> str:
    return ModerationControl(action=action, sanction=sanction, token=token).pack()


async def receipt_markup(session: Any, draft: GroupModerationDraft) -> InlineKeyboardMarkup | None:
    row = await session.get(GroupSanction, draft.sanction_id, populate_existing=True)
    if row is None or row.chat_id != draft.chat_id or row.telegram_id != draft.telegram_id:
        return None
    if draft.kind == "WARN" and row.kind == "WARN" and row.status == "RECORDED":
        return keyboard([[(t("lm.control.reset"), control("reset", row.id))]])
    if (
        draft.kind == row.kind
        and row.kind in {"BAN", "MUTE"}
        and row.status == "ACTIVE"
        and row.owned
    ):
        return keyboard(
            [
                [
                    (
                        t("lm.control.unban" if row.kind == "BAN" else "lm.control.unmute"),
                        control("release", row.id),
                    )
                ]
            ]
        )
    return None


async def public_receipt(
    session: Any, bot: Any, draft: GroupModerationDraft, *, observed: tuple = ()
) -> None:
    from app.bot.local_admin import receipt_text

    if draft.receipt_available_at is not None and utc(draft.receipt_available_at) > now():
        return
    text, done = await receipt_text(session, draft, observed=observed, bot=bot)
    markup = await receipt_markup(session, draft)
    if draft.panel_chat_id == draft.chat_id and draft.panel_message_id:
        try:
            await bot.edit_message_text(
                chat_id=draft.chat_id,
                message_id=draft.panel_message_id,
                text=text,
                reply_markup=markup,
                parse_mode="HTML",
            )
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message.lower():
                raise
    else:
        result = await bot.send_message(draft.chat_id, text, parse_mode="HTML", reply_markup=markup)
        draft.panel_chat_id, draft.panel_message_id = draft.chat_id, result.message_id
    draft.receipt_hash, draft.receipt_done = hashlib.sha256(text.encode()).hexdigest(), done
    await session.commit()


async def bound_origin(
    session: Any, callback: CallbackQuery, sanction: int
) -> tuple[GroupSanction, GroupModerationDraft]:
    if not isinstance(callback.message, Message) or callback.message.chat.type not in {
        "group",
        "supergroup",
    }:
        raise DomainError("group_moderation_stale_control")
    row = await session.get(GroupSanction, sanction, populate_existing=True)
    if row is None or row.chat_id != callback.message.chat.id:
        raise DomainError("group_moderation_stale_control")
    draft = await session.scalar(
        select(GroupModerationDraft)
        .where(
            GroupModerationDraft.sanction_id == row.id,
            GroupModerationDraft.kind == row.kind,
            GroupModerationDraft.panel_chat_id == row.chat_id,
            GroupModerationDraft.panel_message_id == callback.message.message_id,
            GroupModerationDraft.status == "CONFIRMED",
        )
        .limit(1)
    )
    if draft is None:
        raise DomainError("group_moderation_stale_control")
    return row, draft


def register_control_handlers(router: Router) -> None:
    @router.callback_query(ModerationControl.filter())
    async def moderation_control(
        callback: CallbackQuery,
        callback_data: ModerationControl,
        service: Service,
        session_factory: Any,
    ) -> None:
        data = callback_data
        bot = callback.bot
        if bot is None:
            return
        try:
            numeric_id(data.sanction)
            if callback.from_user.is_bot:
                raise DomainError("group_moderation_forbidden")
            actor = callback.from_user.id
            if data.action in {"yes", "no"}:
                prompt = await service.session.get(GroupWarningReset, data.token)
                if (
                    prompt is None
                    or prompt.actor_id != actor
                    or prompt.origin_id != data.sanction
                    or not isinstance(callback.message, Message)
                ):
                    raise DomainError("group_moderation_stale_control")
                row = await service.session.get(GroupSanction, prompt.origin_id)
                if row is None or row.chat_id != callback.message.chat.id:
                    raise DomainError("group_moderation_stale_control")
                local = GroupModerationService(service.session, bot)
                if data.action == "no":
                    from app.moderation_services import lock_group, utc

                    await local._check_group(row.chat_id)
                    await local.authorization.actor(row.chat_id, actor)
                    await lock_group(service.session, row.chat_id)
                    await service.session.refresh(prompt)
                    if prompt.state != "PENDING" or utc(prompt.expires_at) <= now():
                        raise DomainError("group_moderation_stale_control")
                    prompt.state = "CANCELLED"
                    await service.session.commit()
                    await callback.answer(t("lm.control.cancelled"), show_alert=True)
                    return
                await reset_warnings(local, data.token, actor)
                await callback.answer(t("lm.control.reset_done"), show_alert=True)
                from app.bot.local_admin import publish_receipts

                await publish_receipts(session_factory, bot)
                return
            row, draft = await bound_origin(service.session, callback, data.sanction)
            local = GroupModerationService(service.session, bot)
            if data.action == "reset":
                prompt = await reset_prompt(local, row, actor)
                await bot.send_message(
                    row.chat_id,
                    t("lm.control.reset_confirm", count=prompt.expected_count),
                    parse_mode="HTML",
                    ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=actor),
                    reply_markup=keyboard(
                        [
                            [
                                (t("lm.control.yes"), control("yes", row.id, prompt.id)),
                                (t("lm.control.no"), control("no", row.id, prompt.id)),
                            ]
                        ]
                    ),
                )
                await callback.answer()
            elif data.action == "release":
                await local._check_group(row.chat_id)
                await local.authorization.actor(row.chat_id, actor)
                await local.authorization.bot_rights(row.chat_id)
                await local.authorization.target(row.chat_id, row.telegram_id)
                await service.session.refresh(row)
                if row.kind not in {"BAN", "MUTE"} or row.status != "ACTIVE" or not row.owned:
                    raise DomainError("group_moderation_stale_control")
                release = await local.prepare(
                    row.chat_id,
                    actor,
                    row.telegram_id,
                    "UNBAN" if row.kind == "BAN" else "UNMUTE",
                    until_date=None,
                    reason="",
                    request=f"control:{row.id}:{actor}",
                )
                await local.revoke(
                    row.chat_id, actor, row.id, key="local:" + release.id, draft_id=release.id
                )
                draft.receipt_done = True
                release.panel_chat_id, release.panel_message_id = (
                    row.chat_id,
                    draft.panel_message_id,
                )
                draft.panel_chat_id, draft.panel_message_id = None, None
                await service.session.commit()
                await callback.answer()
                from app.bot.local_moderation import LocalModerationExecutor

                await LocalModerationExecutor(session_factory, bot).process(
                    limit=2, sanction_id=row.id
                )
            else:
                raise DomainError("group_moderation_stale_control")
        except TelegramRetryAfter:
            await service.session.rollback()
            return
        except (DomainError, TelegramAPIError, TimeoutError, OSError, ValueError) as error:
            await service.session.rollback()
            key = "stale"
            if isinstance(error, DomainError):
                key = (
                    "rights"
                    if error.code in {"group_moderation_forbidden", "group_moderation_bot_rights"}
                    else "busy"
                    if error.code in {"group_moderation_uncertain", "group_moderation_busy"}
                    else "stale"
                )
            try:
                await callback.answer(t("lm.control." + key), show_alert=True)
            except (TelegramAPIError, TimeoutError, OSError):
                pass


async def publish_warning_announcements(sessions: Any, bot: Any) -> None:
    """Claim before send: an ambiguous send outcome is never blindly duplicated."""
    async with sessions() as session:
        announcement = aliased(GroupModerationDraft)
        events = list(
            await session.scalars(
                select(GroupWarningEscalation)
                .join(
                    GroupModerationDraft,
                    GroupModerationDraft.sanction_id == GroupWarningEscalation.trigger_id,
                )
                .join(announcement, announcement.id == GroupWarningEscalation.draft_id)
                .where(
                    announcement.panel_message_id.is_(None),
                    announcement.receipt_checked_at.is_(None),
                    or_(
                        announcement.receipt_available_at.is_(None),
                        announcement.receipt_available_at <= now(),
                    ),
                    GroupWarningEscalation.state == "SUCCEEDED",
                    GroupModerationDraft.panel_chat_id == GroupWarningEscalation.chat_id,
                    GroupModerationDraft.panel_message_id.is_not(None),
                )
                .limit(10)
            )
        )
        await session.commit()
        for event in events:
            draft = await session.get(GroupModerationDraft, event.draft_id)
            if draft is None or draft.panel_message_id or draft.receipt_checked_at is not None:
                continue
            claimed = await session.execute(
                update(GroupModerationDraft)
                .where(
                    GroupModerationDraft.id == draft.id,
                    GroupModerationDraft.panel_message_id.is_(None),
                    GroupModerationDraft.receipt_checked_at.is_(None),
                )
                .values(receipt_checked_at=now())
            )
            await session.commit()
            if not claimed.rowcount:
                continue
            try:
                with use_language(draft.language):
                    await public_receipt(session, bot, draft)
            except TelegramRetryAfter as error:
                from datetime import timedelta

                await session.rollback()
                await session.refresh(draft)
                draft.receipt_checked_at = None
                draft.receipt_available_at = now() + timedelta(seconds=error.retry_after)
                await session.commit()
            except (TelegramAPIError, TimeoutError, OSError):
                await session.rollback()
                # Keep the claim as evidence of a potentially delivered message.

"""Recipient-only confirmation for a Telegram restriction without local ownership."""

from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.bot.callbacks import ModerationControl
from app.bot.keyboards import keyboard
from app.bot.moderation_errors import basic_kind
from app.bot.moderation_messages import BRAND, identity_fields, identity_labels
from app.bot.private_panels import acknowledge, dismiss, ephemeral_id, private_message_key, show
from app.errors import DomainError
from app.external_releases import intent
from app.i18n import language, t
from app.models import GroupModerationDraft, now
from app.moderation_services import GroupModerationService, utc
from app.services import Service


async def confirmation(message: Message, service: Service, draft: GroupModerationDraft) -> None:
    # A redelivered Telegram update must not open another confirmation window.
    if draft.receipt_visibility == "PRIVATE" and draft.panel_message_id:
        return
    labels = await identity_labels(
        service.session, {draft.telegram_id}, bot=message.bot, chat=draft.chat_id
    )
    text = (
        t("lm.control.external_title")
        + "\n\n"
        + identity_fields(draft.telegram_id, labels[draft.telegram_id])
        + "\n\n"
        + t("lm.control.external_confirm", action=draft.kind)
        + "\n\n"
        + BRAND
    )
    sent = await show(
        message,
        draft.actor_id,
        text,
        keyboard(
            [
                [
                    (
                        t("lm.confirm"),
                        ModerationControl(action="external_yes", sanction=0, token=draft.id).pack(),
                    ),
                    (
                        t("lm.cancel"),
                        ModerationControl(action="external_no", sanction=0, token=draft.id).pack(),
                    ),
                ]
            ]
        ),
        edit=False,
    )
    if sent is not None:
        draft.panel_chat_id, draft.panel_message_id = sent.chat.id, private_message_key(sent)
        draft.receipt_visibility, draft.language = "PRIVATE", language.get()
        await service.session.commit()


def register_external_release_handlers(router: Router) -> None:
    @router.callback_query(ModerationControl.filter(F.action.in_({"external_yes", "external_no"})))
    async def external_release(
        callback: CallbackQuery,
        callback_data: ModerationControl,
        service: Service,
        session_factory: Any,
    ) -> None:
        actor, acknowledged = callback.from_user.id, False
        try:
            draft = await service.session.get(
                GroupModerationDraft, callback_data.token, populate_existing=True
            )
            if (
                draft is None
                or callback_data.sanction != 0
                or draft.actor_id != actor
                or callback.from_user.is_bot
                or not isinstance(callback.message, Message)
                or callback.message.chat.id not in {actor, draft.chat_id}
                or await intent(service.session, draft) is None
            ):
                raise DomainError("group_moderation_stale_control")
            ephemeral_id(callback.message, actor)
            if draft.status != "PENDING" or utc(draft.expires_at) <= now():
                await dismiss(callback.message, actor)
                raise DomainError("group_moderation_stale_control")
            if draft.receipt_visibility != "PRIVATE" or (
                draft.panel_chat_id,
                draft.panel_message_id,
            ) != (callback.message.chat.id, private_message_key(callback.message)):
                raise DomainError("group_moderation_stale_control")
            local = GroupModerationService(service.session, callback.bot)
            if callback_data.action == "external_no":
                await local.cancel_draft(draft.id, actor)
                await dismiss(callback.message, actor)
                await acknowledge(callback, t("lm.control.cancelled"), alert=True)
                return
            row = await local.confirm(draft.id, actor)
            await acknowledge(callback)
            acknowledged = True
            from app.bot.local_moderation import LocalModerationExecutor
            from app.bot.moderation_controls import public_receipt

            await LocalModerationExecutor(session_factory, callback.bot).process(
                limit=2, sanction_id=row.id
            )
            await service.session.refresh(draft)
            await public_receipt(service.session, callback.bot, draft)
        except (DomainError, TelegramAPIError, TimeoutError, OSError, ValueError) as error:
            await service.session.rollback()
            if not acknowledged:
                key = basic_kind(error) or "check_unavailable"
                await acknowledge(callback, t("lm.diagnostic." + key + ".title"), alert=True)

"""Durable, bounded Telegram moderation and opt-in notification delivery worker."""

import asyncio
from collections.abc import Callable
from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from app.bot.group_presentation import recovery_notification
from app.config import Settings
from app.group_services import GroupService
from app.i18n import t, use_language
from app.models import ManagedGroup, RecoveryCampaign
from app.repositories import Repository


async def process_group_jobs(bot: Bot, settings: Settings, sessions: Any) -> int:
    log = structlog.get_logger()
    processed = 0
    async with sessions() as session:
        service = GroupService(settings, session)
        bans = await service.claim_bans(limit=5)
        for job in bans:
            if not await service.ban_eligible(job.id):
                continue
            success, result = False, "TelegramAPIError"
            retry_after, permanent = None, False
            try:
                success = await bot.ban_chat_member(chat_id=job.chat_id, user_id=job.telegram_id)
                result = "BANNED" if success else "API_FALSE"
            except TelegramRetryAfter as error:
                result = "TelegramRetryAfter"
                retry_after = error.retry_after
            except TelegramForbiddenError:
                result = "TelegramForbiddenError"
                permanent = True
                await service.disable_group(job.chat_id)
            except TelegramBadRequest:
                result = "TelegramBadRequest"
                permanent = True
            except TelegramAPIError as error:
                result = type(error).__name__
            await service.finish_ban(
                job.id, success, result, retry_after=retry_after, permanent=permanent
            )
            log.info(
                "group_ban_result", chat_id=job.chat_id, user_id=job.telegram_id, result=result
            )
            processed += 1
        deliveries = await service.claim_deliveries(limit=5)
        for delivery in deliveries:
            if not await service.delivery_eligible(delivery.id):
                continue
            campaign = await session.get(RecoveryCampaign, delivery.campaign_id)
            if campaign is None:
                await service.finish_delivery(delivery.id, False, "OBSOLETE", permanent=True)
                continue
            group = await session.get(ManagedGroup, campaign.chat_id)
            selected_language = await Repository(session).language(delivery.telegram_id)
            title = group.title if group else t("core.group", selected_language)
            success, result = False, "TelegramAPIError"
            retry_after, permanent = None, False
            try:
                with use_language(selected_language):
                    await bot.send_message(
                        delivery.telegram_id, recovery_notification(title, campaign.invite_url)
                    )
                success, result = True, "SENT"
            except TelegramRetryAfter as error:
                result = "TelegramRetryAfter"
                retry_after = error.retry_after
            except TelegramForbiddenError:
                result = "TelegramForbiddenError"
                permanent = True
                await service.subscribe(delivery.telegram_id, campaign.chat_id, False)
            except TelegramAPIError as error:
                result = type(error).__name__
            await service.finish_delivery(
                delivery.id, success, result, retry_after=retry_after, permanent=permanent
            )
            log.info("group_recovery_result", user_id=delivery.telegram_id, result=result)
            processed += 1
    return processed


async def group_worker(
    bot: Bot, settings: Settings, sessions: Any, *, on_progress: Callable[[], None] | None = None
) -> None:
    log = structlog.get_logger()
    while True:
        try:
            processed = await process_group_jobs(bot, settings, sessions)
            if on_progress is not None:
                on_progress()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.error("group_worker_failed", exception_type=type(error).__name__)
            processed = 0
        await asyncio.sleep(1 if processed else 5)

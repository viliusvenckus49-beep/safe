"""Durable, bounded Telegram moderation and opt-in notification delivery worker."""

import asyncio
import re
from collections.abc import Callable
from time import monotonic
from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.group_presentation import recovery_notification
from app.bot.moderation_notices import notify_ban_failure
from app.config import Settings
from app.group_services import BanSummary, GroupService, enqueue_scam_bans
from app.i18n import t, use_language
from app.models import BanAction, ManagedGroup, RecoveryCampaign, ScamRecord
from app.mtproto_relay import active_relay
from app.repositories import Repository

BAN_API_TIMEOUT = 10.0
INTERACTIVE_BAN_BUDGET = 20.0


def _api_reason(error: TelegramAPIError) -> str:
    """Keep Telegram's description, excluding tokens and control characters."""
    reason = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "[redacted]", error.message)
    reason = re.sub(r"https?://\S+", "[redacted URL]", reason)
    return re.sub(r"[\x00-\x1f\x7f]", " ", reason)[:256]


async def _attempt_ban(bot: Bot, job: BanAction, timeout: float) -> tuple[bool, str]:
    lookup = getattr(bot, "get_chat_member", None)
    if lookup is not None:
        try:
            member = await asyncio.wait_for(
                lookup(chat_id=job.chat_id, user_id=job.telegram_id),
                timeout=min(2.0, timeout / 2),
            )
            if getattr(member, "status", None) == "kicked":
                return True, "ALREADY_BANNED"
        except TelegramRetryAfter:
            raise  # Respect Telegram's delay for reads as well as writes.
        except (TelegramAPIError, TimeoutError, OSError) as error:
            structlog.get_logger().info(
                "group_ban_precheck_failed",
                chat_id=job.chat_id,
                user_id=job.telegram_id,
                exception_type=type(error).__name__,
            )
            # A failed lookup does not prove a preemptive ban is impossible.
    success = await bot.ban_chat_member(chat_id=job.chat_id, user_id=job.telegram_id) is True
    return success, "BANNED" if success else "API_FALSE"


async def _execute_bans(
    bot: Bot, service: GroupService, bans: list[BanAction], *, timeout: float = BAN_API_TIMEOUT
) -> int:
    log = structlog.get_logger()
    processed = 0
    for job in bans:
        if not await service.ban_eligible(job.id):
            continue
        success, result = False, "TelegramAPIError"
        retry_after, permanent = None, False
        reason = None
        relay = active_relay()
        try:
            success, result = await asyncio.wait_for(
                (
                    relay.attempt_ban(bot, service, job, timeout)
                    if service.settings.group_help_enabled and relay is not None
                    else _attempt_ban(bot, job, timeout)
                ),
                timeout=timeout,
            )
        except TelegramRetryAfter as error:
            result, reason = "TelegramRetryAfter", _api_reason(error)
            retry_after = error.retry_after
        except TelegramForbiddenError as error:
            result, reason = "TelegramForbiddenError", _api_reason(error)
            permanent = True
            await service.note_group_permissions(job.chat_id, False)
        except TelegramBadRequest as error:
            result, reason = "TelegramBadRequest", _api_reason(error)
            # Telegram may learn this numeric peer through another protected group.
            # Keep identity-resolution refusals eligible for bounded outbox retries.
            permanent = not any(
                code in error.message.upper()
                for code in ("PARTICIPANT_ID_INVALID", "USER_NOT_PARTICIPANT")
            )
        except TelegramAPIError as error:
            result, reason = type(error).__name__, _api_reason(error)
        except (TimeoutError, OSError) as error:
            result = type(error).__name__
        await service.finish_ban(
            job.id, success, result, retry_after=retry_after, permanent=permanent, reason=reason
        )
        log.info(
            "group_ban_result",
            operation="ban_chat_member",
            chat_id=job.chat_id,
            user_id=job.telegram_id,
            scam_record_id=job.scam_record_id,
            success=success,
            pending=not success,
            result=result,
            error_type=None if success else result,
            reason=reason,
        )
        processed += 1
        if not success:
            # Alert rollback must not expire the worker's other claimed ban jobs.
            async with AsyncSession(
                bind=service.session.bind, expire_on_commit=False
            ) as alert_session:
                await notify_ban_failure(
                    bot,
                    GroupService(service.settings, alert_session),
                    job.chat_id,
                    job.telegram_id,
                    record_id=job.scam_record_id,
                    result=result,
                    reason=reason,
                )
    return processed


async def process_scam_bans(
    bot: Bot, settings: Settings, sessions: Any, record_id: int
) -> BanSummary:
    """Initiate committed SCAM protection now; retain unprocessed work in the outbox."""
    relay = active_relay()
    if settings.group_help_enabled and relay is not None:
        await relay.resolve_record(record_id)
    async with sessions() as session:
        service = GroupService(settings, session)
        record = await session.get(ScamRecord, record_id, populate_existing=True)
        if record is not None:
            await enqueue_scam_bans(session, record)
        await session.commit()
        # Claim only the next immediate attempt. Overflow stays PENDING rather
        # than holding a PROCESSING lease throughout a long interactive batch.
        deadline = monotonic() + INTERACTIVE_BAN_BUDGET
        for _ in range(100):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            bans = await service.claim_bans(limit=1, scam_record_id=record_id)
            if not bans:
                if await service.has_ready_bans(record_id):
                    continue
                break
            await _execute_bans(bot, service, bans, timeout=min(BAN_API_TIMEOUT, remaining))
        return await service.ban_summary(record_id)


async def process_group_jobs(bot: Bot, settings: Settings, sessions: Any) -> int:
    log = structlog.get_logger()
    processed = 0
    relay = active_relay()
    if settings.group_help_enabled and relay is not None:
        processed += await relay.resolve_pending()
    async with sessions() as session:
        service = GroupService(settings, session)
        bans = await service.claim_bans(limit=5)
        processed += await _execute_bans(bot, service, bans)
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

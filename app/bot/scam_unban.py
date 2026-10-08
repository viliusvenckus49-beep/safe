"""Administrator-requested unblocking; retain records and actual audited outcomes."""

import asyncio
from datetime import UTC, timedelta
from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import select, update

from app.bot.group_runtime import _api_reason
from app.bot.moderation_lock import user_moderation_lock
from app.errors import DomainError
from app.models import AuditEvent, BanAction, ManagedGroup, now
from app.mtproto_relay import active_relay
from app.services import Service

UNBAN_TIMEOUT = 5.0
UNBAN_COOLDOWN = 10


async def eligible(core: Service, actor: int, record_id: int):
    await core.require_admin(actor)
    record = await core.repo.scam_by_id(record_id)
    if record is None or record.target.telegram_id is None:
        raise DomainError("stale_callback")
    current = await core.repo.active_scam(record.target_id)
    if current is not None and current.id != record.id:
        raise DomainError("stale_callback")
    return record


async def process_scam_unban(
    bot: Bot, core: Service, record_id: int, actor: int
) -> tuple[Any, dict]:
    record = await eligible(core, actor, record_id)
    user_id = record.target.telegram_id
    await core.session.commit()
    async with user_moderation_lock(user_id):
        await core.repo.lock_identity_metadata()
        record = await eligible(core, actor, record_id)
        latest = await core.session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == "scam_unban_requested",
                AuditEvent.details["record_id"].as_integer() == record_id,
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        if latest is not None and latest.created_at.replace(tzinfo=UTC) > now() - timedelta(
            seconds=UNBAN_COOLDOWN
        ):
            await core.session.rollback()
            raise DomainError("cooldown")
        delayed = await core.session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == "scam_unban_attempt",
                AuditEvent.details["record_id"].as_integer() == record_id,
                AuditEvent.details["retry_after"].as_integer() > 0,
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )
        if (
            delayed is not None
            and delayed.created_at.replace(tzinfo=UTC)
            + timedelta(seconds=int(delayed.details["retry_after"]))
            > now()
        ):
            await core.session.rollback()
            raise DomainError("cooldown")
        await core.session.commit()
        if record.status == "ACTIVE":
            if not await core.remove_scam(
                actor,
                f"u:{record.target_id}",
                "Administrator UNBAN via SCAM registry",
                record_id=record.id,
            ):
                raise DomainError("stale_callback")
        await core._admin_write_lock(actor)
        record = await eligible(core, actor, record_id)
        await core.session.execute(
            update(BanAction)
            .where(
                BanAction.scam_record_id == record_id,
                BanAction.status.in_(["PENDING", "FAILED", "PROCESSING"]),
            )
            .values(status="OBSOLETE", completed_at=now())
        )
        core._audit(actor, "scam_unban_requested", record.target_id, record_id=record_id)
        groups = list(
            (
                await core.session.scalars(
                    select(ManagedGroup)
                    .where(ManagedGroup.approved.is_(True), ManagedGroup.enabled.is_(True))
                    .order_by(ManagedGroup.chat_id)
                )
            ).all()
        )
        await core.session.commit()
        relay = active_relay()
        staff = "disabled"
        if core.settings.group_help_enabled:
            staff = "unavailable"
            if relay is not None:
                try:
                    staff = (
                        "submitted"
                        if await asyncio.wait_for(relay.dispatch_unban(actor, record_id), 10)
                        else "unavailable"
                    )
                except (TelegramAPIError, TimeoutError, OSError):
                    staff = "unavailable"
        outcomes = []
        retry_after = None
        for group in groups:
            # Recheck permissions, the selected activation and group withdrawal
            # between external calls; an old button never unbans a new activation.
            await eligible(core, actor, record_id)
            await core.session.refresh(group)
            if not group.approved or not group.enabled:
                await core.session.commit()
                continue
            await core.session.commit()
            result, reason = "TelegramRetryAfter", None
            success = False
            if retry_after is None:
                try:
                    success, result = await asyncio.wait_for(
                        attempt_unban(bot, group.chat_id, user_id), UNBAN_TIMEOUT
                    )
                except TelegramRetryAfter as error:
                    retry_after, reason = error.retry_after, _api_reason(error)
                except TelegramAPIError as error:
                    result, reason = type(error).__name__, _api_reason(error)
                except (TimeoutError, OSError) as error:
                    result = type(error).__name__
            outcome = dict(
                chat_id=group.chat_id,
                title=group.title,
                success=success,
                result=result,
                reason=reason,
                retry_after=retry_after,
            )
            outcomes.append(outcome)
            core._audit(
                actor,
                "scam_unban_attempt",
                record.target_id,
                record_id=record_id,
                user_id=user_id,
                **outcome,
            )
            await core.session.commit()
            structlog.get_logger().info(
                "group_unban_result",
                operation="unban_chat_member",
                record_id=record_id,
                user_id=user_id,
                **outcome,
            )
        await core.session.refresh(record, attribute_names=["target"])
        return record, {"groups": outcomes, "staff": staff}


async def attempt_unban(bot: Bot, chat_id: int, user_id: int) -> tuple[bool, str]:
    try:
        member = await asyncio.wait_for(bot.get_chat_member(chat_id=chat_id, user_id=user_id), 1)
        if member.status in {"member", "administrator", "creator", "restricted", "left"}:
            return True, "ALREADY_UNBANNED"
    except TelegramRetryAfter:
        raise
    except (TelegramAPIError, TimeoutError, OSError):
        pass
    # only_if_banned avoids removing someone who has already rejoined the group.
    if await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True) is True:
        return True, "UNBANNED"
    return False, "API_FALSE"

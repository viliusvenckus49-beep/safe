"""Registration announcements reuse the persisted, claimed audit-notice mechanism."""

import asyncio
from time import monotonic
from typing import Any

import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import BigInteger, cast, select

from app import presentation as p
from app.config import Settings
from app.group_services import GroupService
from app.i18n import use_language
from app.models import AuditEvent, ManagedGroup, now
from app.repositories import Repository

ACTION = "scam_group_notice"
LEASE_SECONDS = 120
DELIVERY_BUDGET = 10.0


async def queue_scam_announcements(
    settings: Settings, sessions: Any, record_id: int, origin_chat_id: int | None = None
) -> None:
    """One notice per registration and active group; the origin gets its existing receipt."""
    async with sessions() as session:
        repo = Repository(session)
        await repo.lock_identity_metadata()
        record = await repo.scam_by_id(record_id)
        if record is None or record.status != "ACTIVE":
            await session.commit()
            return
        groups = await session.scalars(
            select(ManagedGroup.chat_id).where(
                ManagedGroup.approved.is_(True),
                ManagedGroup.enabled.is_(True),
                ~GroupService.removed_group(ManagedGroup.chat_id),
            )
        )
        existing = set(
            await session.scalars(
                select(cast(AuditEvent.details["chat_id"].as_string(), BigInteger)).where(
                    AuditEvent.action == ACTION,
                    AuditEvent.details["record_id"].as_integer() == record_id,
                )
            )
        )
        lang = await repo.language(record.moderator_id)
        for chat_id in groups:
            if chat_id in existing:
                continue
            session.add(
                AuditEvent(
                    actor_id=record.moderator_id,
                    action=ACTION,
                    target_id=record.target_id,
                    details={
                        "record_id": record_id,
                        "chat_id": chat_id,
                        "language": lang,
                        "status": "SOURCE" if chat_id == origin_chat_id else "PENDING",
                        "attempts": 0,
                        "next_attempt_at": int(now().timestamp()),
                    },
                )
            )
        await session.commit()


async def process_scam_announcements(bot: Any, settings: Settings, sessions: Any) -> int:
    """Bounded retries release database locks before Telegram and honor flood waits."""
    from app.bot.scam_admin import refresh_controls

    processed = 0
    deadline = monotonic() + DELIVERY_BUDGET
    for _ in range(5):
        if monotonic() >= deadline:
            break
        async with sessions() as session:
            repo = Repository(session)
            await repo.lock_identity_metadata()
            timestamp = int(now().timestamp())
            notice = await session.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == ACTION,
                    AuditEvent.details["status"]
                    .as_string()
                    .in_(["PENDING", "RETRY", "PROCESSING"]),
                    cast(AuditEvent.details["next_attempt_at"].as_string(), BigInteger)
                    <= timestamp,
                )
                .order_by(AuditEvent.id)
                .limit(1)
            )
            if notice is None:
                await session.commit()
                break
            details = notice.details
            chat_id, record_id = details["chat_id"], details["record_id"]
            record = await repo.scam_by_id(record_id)
            group = await session.get(ManagedGroup, chat_id, populate_existing=True)
            groups = GroupService(settings, session)
            if (
                record is None
                or record.status != "ACTIVE"
                or group is None
                or not group.approved
                or not group.enabled
                or await groups.is_removed_group(chat_id)
            ):
                notice.details = {**details, "status": "CANCELLED"}
                await session.commit()
                processed += 1
                continue
            attempts = details["attempts"] + 1
            notice.details = {
                **details,
                "status": "PROCESSING",
                "attempts": attempts,
                "next_attempt_at": timestamp + LEASE_SECONDS,
            }
            summary = await groups.ban_summary(record_id)
            await session.refresh(record, attribute_names=["target"])
            with use_language(details["language"]):
                text = p.scam_registered(
                    record.target,
                    groups=summary.checked,
                    succeeded=summary.succeeded,
                    failed=summary.failed,
                    pending=summary.pending,
                    already_banned=summary.already_banned,
                )
                markup = refresh_controls(record)
            await session.commit()
            status, result, delay = "SENT", "SENT", 0
            message_id = None
            try:
                sent = await asyncio.wait_for(
                    bot.send_message(chat_id, text, reply_markup=markup),
                    timeout=max(0.001, deadline - monotonic()),
                )
                message_id = getattr(sent, "message_id", None)
            except TelegramRetryAfter as error:
                status, result, delay = "RETRY", "TelegramRetryAfter", error.retry_after
            except (TelegramForbiddenError, TelegramBadRequest) as error:
                status, result = "FAILED", type(error).__name__
            except Exception as error:
                status = "RETRY" if attempts < 6 else "FAILED"
                result, delay = type(error).__name__, min(1800, 30 * 2 ** (attempts - 1))
            notice.details = {
                **notice.details,
                "status": status,
                "result": result,
                "message_id": message_id,
                "next_attempt_at": int(now().timestamp()) + delay,
            }
            await session.commit()
            structlog.get_logger().info(
                "scam_group_notice_result",
                operation="send_message",
                chat_id=chat_id,
                user_id=record.target.telegram_id,
                scam_record_id=record_id,
                success=status == "SENT",
                result=result,
            )
            processed += 1
    return processed

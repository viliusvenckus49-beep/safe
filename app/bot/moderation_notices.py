"""One private owner alert per SCAM activation and group; never post failures publicly."""

import asyncio
from html import escape
from typing import Any

import structlog
from sqlalchemy import BigInteger, cast, select

from app.group_services import GroupService
from app.i18n import t
from app.models import AuditEvent, BanAction, ManagedGroup
from app.telegram_failures import ban_failure


def reason_key(result: str, reason: str | None) -> str:
    code = ban_failure(result, reason)
    return (
        "ban_alert." + code
        if code in {"admin", "rights", "unknown", "rate_limit", "temporary"}
        else "diagnostic.ban_" + code
    )


async def notify_ban_failure(
    bot: Any,
    service: GroupService,
    chat_id: int,
    telegram_id: int,
    *,
    record_id: int | None = None,
    result: str = "UpdateError",
    reason: str | None = None,
    present: bool = False,
) -> None:
    """Persist an at-most-once claim before sending; release DB locks before Telegram."""
    owner = service.settings.group_owner
    if owner is None:
        return
    session, repo = service.session, service.core.repo
    log = structlog.get_logger()
    try:
        await repo.lock_identity_metadata()
        user = await repo.user_by_telegram(telegram_id)
        record = await repo.active_scam(user.id) if user else None
        group = await session.get(ManagedGroup, chat_id, populate_existing=True)
        if (
            record is None
            or (record_id is not None and record.id != record_id)
            or group is None
            or not group.approved
            or not group.enabled
        ):
            await session.commit()
            return
        filters = (
            AuditEvent.target_id == record.target_id,
            cast(AuditEvent.details["chat_id"].as_string(), BigInteger) == chat_id,
            AuditEvent.details["record_id"].as_integer() == record.id,
        )
        if (
            not present
            and await session.scalar(
                select(AuditEvent.id)
                .where(AuditEvent.action == "scam_member_present", *filters)
                .limit(1)
            )
            is None
        ):
            await session.commit()
            return
        # A background worker may have completed the ban while this update failed.
        state = await session.scalar(
            select(BanAction.status).where(
                BanAction.chat_id == chat_id,
                BanAction.telegram_id == telegram_id,
                BanAction.scam_record_id == record.id,
            )
        )
        if (
            state == "SUCCEEDED"
            or await session.scalar(
                select(AuditEvent.id)
                .where(AuditEvent.action == "scam_ban_alert", *filters)
                .limit(1)
            )
            is not None
        ):
            await session.commit()
            return
        lang = await repo.language(owner)
        assert user is not None
        name = "@" + user.username if user.username else user.display_name
        notice = AuditEvent(
            actor_id=owner,
            action="scam_ban_alert",
            target_id=record.target_id,
            details={"chat_id": chat_id, "record_id": record.id, "status": "CLAIMED"},
        )
        session.add(notice)
        await session.flush()
        text = t(
            "ban_alert.message",
            lang=lang,
            name=escape(name),
            telegram_id=telegram_id,
            group=escape(group.title),
            chat_id=chat_id,
            reason=t(reason_key(result, reason), lang=lang),
        )
        await session.commit()
        sent = False
        try:
            await asyncio.wait_for(bot.send_message(owner, text), timeout=10)
            sent = True
        except Exception as error:
            log.warning(
                "ban_alert_delivery_failed",
                chat_id=chat_id,
                user_id=telegram_id,
                exception_type=type(error).__name__,
            )
        notice.details = {**notice.details, "status": "SENT" if sent else "FAILED"}
        await session.commit()
    except Exception as error:
        await session.rollback()
        log.warning(
            "ban_alert_failed",
            chat_id=chat_id,
            user_id=telegram_id,
            exception_type=type(error).__name__,
        )

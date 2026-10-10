"""SCAM receipts use the shared durable executor and its actual group results."""

from typing import Any

import structlog
from aiogram import Bot

from app import presentation as p
from app.bot.group_runtime import process_scam_bans
from app.group_services import GroupService
from app.models import ScamRecord
from app.services import Service


async def registered_scam_text(
    bot: Bot | None,
    core: Service,
    sessions: Any,
    record: ScamRecord,
    *,
    announce: bool = False,
    origin_chat_id: int | None = None,
) -> str:
    if announce:
        from app.bot.scam_broadcasts import queue_scam_announcements

        try:
            await queue_scam_announcements(core.settings, sessions, record.id, origin_chat_id)
        except Exception as error:
            structlog.get_logger().warning(
                "scam_group_notice_queue_failed",
                scam_record_id=record.id,
                exception_type=type(error).__name__,
            )
    try:
        if bot is None:
            async with sessions() as session:
                summary = await GroupService(core.settings, session).ban_summary(record.id)
        else:
            summary = await process_scam_bans(bot, core.settings, sessions, record.id)
    except Exception as error:
        # Registration was already committed. Its durable ban queue remains available.
        structlog.get_logger().warning(
            "scam_ban_processing_deferred",
            operation="ban_chat_member",
            scam_record_id=record.id,
            user_id=record.target.telegram_id,
            exception_type=type(error).__name__,
        )
        async with sessions() as session:
            summary = await GroupService(core.settings, session).ban_summary(record.id)
    await core.session.refresh(record, attribute_names=["target", "target_id"])
    return p.scam_registered(
        record.target,
        groups=summary.checked,
        succeeded=summary.succeeded,
        failed=summary.failed,
        pending=summary.pending,
        already_banned=summary.already_banned,
    )

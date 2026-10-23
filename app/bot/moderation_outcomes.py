"""Separate successful public receipts from private, evidence-based diagnostics."""

from typing import Any

from sqlalchemy import select

from app.bot.moderation_errors import basic_kind, diagnose_state, error_message
from app.errors import DomainError
from app.models import GroupModerationDraft, GroupSanction, GroupSanctionJob, GroupWarningEscalation
from app.moderation_services import GroupModerationService


async def diagnostic(session: Any, bot: Any, draft: GroupModerationDraft) -> str | None:
    row = await session.get(GroupSanction, draft.sanction_id, populate_existing=True)
    command = draft.original_command or "/" + draft.kind.lower()
    key = "check_unavailable"
    if row is not None:
        if row.kind == "WARN" and row.status in {"RECORDED", "CANCELLED", "COMPLETED"}:
            event = await session.scalar(
                select(GroupWarningEscalation).where(GroupWarningEscalation.trigger_id == row.id)
            )
            if event is not None and event.state == "PENDING":
                return error_message(command, "queued", fallback="")
            return None
        jobs = list(
            await session.scalars(
                select(GroupSanctionJob)
                .where(GroupSanctionJob.sanction_id == row.id)
                .execution_options(populate_existing=True)
            )
        )
        release = draft.kind in {"UNBAN", "UNMUTE"} or row.kind == "KICK"
        job = next((j for j in jobs if j.operation == ("RELEASE" if release else "APPLY")), None)
        if row.status == "UNCERTAIN":
            key = "uncertain"
        elif job and job.status == "SUCCEEDED" and job.result in {"APPLIED", "RELEASED"}:
            return None
        elif job and job.result in {
            "ALREADY_BANNED",
            "ALREADY_CLEAR",
            "group_moderation_existing_restriction",
        }:
            key = await diagnose_state(
                GroupModerationService(session, bot),
                row.chat_id,
                draft.actor_id,
                row.telegram_id,
                command,
            )
        elif job and job.result == "SCAM_PROTECTED":
            key = "scam"
        elif job and job.result == "NOT_OWNED":
            key = "foreign"
        elif job and job.result == "RESTRICTION_CHANGED":
            key = "changed"
        elif (
            job and job.status in {"PENDING", "PROCESSING"} or row.status in {"PENDING", "REVOKING"}
        ):
            key = "queued"
        elif job and job.result in {"TelegramBadRequest", "TelegramForbiddenError", "API_FALSE"}:
            key = "api_rejected"
        elif job and job.result in {
            "REVOKED_BEFORE_APPLY",
            "NOT_APPLIED",
            "NO_CONFIRMED_RESTRICTION",
            "POLICY_OR_DEADLINE_CHANGED",
            "POLICY_CHANGED",
        }:
            key = "cancelled"
        elif job and job.result:
            key = basic_kind(DomainError(job.result)) or "check_unavailable"
    return error_message(command, key, fallback="")

"""Explicit, durable approval for releasing an existing Telegram restriction.

Untracked restrictions are never relabelled as SAFECheck-owned. The existing
actor-bound draft and append-only audit events bind approval to a fresh snapshot.
"""

from typing import Any

from aiogram.types import ChatPermissions
from sqlalchemy import select, update

from app.errors import DomainError
from app.models import AuditEvent, GroupModerationDraft, GroupSanction, GroupSanctionJob, now
from app.moderation_services import LIVE, audit, lock_group, utc
from app.repositories import Repository

PREPARED = "group_moderation_external_release_prepared"
CONFIRMED = "group_moderation_external_release_confirmed"


def snapshot(member: Any, kind: str) -> dict | None:
    expected = "kicked" if kind == "UNBAN" else "restricted"
    if getattr(member, "status", None) != expected:
        return None
    until = getattr(member, "until_date", None)
    result: dict[str, Any] = {
        "status": expected,
        "until": int(until.timestamp()) if until and until.timestamp() > 0 else None,
    }
    if kind == "UNMUTE":
        result["permissions"] = {
            key: getattr(member, key, None)
            for key in ChatPermissions.model_fields
            if key.startswith("can_")
        }
        result["is_member"] = getattr(member, "is_member", None)
    return result


async def intent(session: Any, draft: GroupModerationDraft) -> AuditEvent | None:
    event = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.action == PREPARED,
            AuditEvent.details["draft_id"].as_string() == draft.id,
        )
        .order_by(AuditEvent.id.desc())
        .limit(1)
    )
    if event is not None and (
        event.actor_id != draft.actor_id
        or event.details.get("chat_id") != draft.chat_id
        or event.details.get("user_id") != draft.telegram_id
        or event.details.get("kind") != draft.kind
    ):
        raise DomainError("group_moderation_invalid_draft")
    return event


async def reconcile(local: Any, chat: int, target: int, member: Any) -> None:
    """Retire a definitively changed ACTIVE record while holding the group lock.

    This only updates local bookkeeping. It neither removes Telegram restrictions
    nor resolves uncertain/in-flight writes, and it retains every historical row.
    """
    rows = list(
        await local.session.scalars(
            select(GroupSanction).where(
                GroupSanction.chat_id == chat,
                GroupSanction.telegram_id == target,
                GroupSanction.status == "ACTIVE",
                GroupSanction.kind.in_(("BAN", "KICK", "MUTE")),
            )
        )
    )
    for row in rows:
        in_flight = await local.session.scalar(
            select(GroupSanctionJob.id)
            .where(
                GroupSanctionJob.sanction_id == row.id,
                GroupSanctionJob.status.in_(("PROCESSING", "UNCERTAIN")),
            )
            .limit(1)
        )
        replacing = await local.session.scalar(
            select(GroupSanction.id)
            .where(
                GroupSanction.replaces_sanction_id == row.id,
                GroupSanction.status.in_(LIVE),
            )
            .limit(1)
        )
        if in_flight is not None or replacing is not None:
            continue
        observed = snapshot(member, "UNBAN" if row.kind in {"BAN", "KICK"} else "UNMUTE")
        until = int(utc(row.until_date).timestamp()) if row.until_date else None
        if (
            observed is not None
            and observed["until"] == until
            and (
                row.kind != "MUTE"
                or all(value is False for value in observed["permissions"].values())
            )
        ):
            continue
        if getattr(member, "status", None) not in {"member", "left", "kicked", "restricted"}:
            continue
        row.status = "EXPIRED" if row.until_date and utc(row.until_date) <= now() else "COMPLETED"
        row.updated_at = now()
        await local.session.execute(
            update(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == row.id,
                GroupSanctionJob.status == "PENDING",
            )
            .values(status="CANCELLED", result="TELEGRAM_STATE_CHANGED", updated_at=now())
        )
        await local.session.execute(
            update(GroupModerationDraft)
            .where(
                GroupModerationDraft.sanction_id == row.id,
                GroupModerationDraft.panel_message_id.is_not(None),
            )
            .values(receipt_done=False, receipt_checked_at=None)
        )
        audit(
            local.session,
            "restriction_reconciled",
            0,
            chat,
            user_id=target,
            sanction_id=row.id,
            observed_status=member.status,
            status=row.status,
        )


async def prepare(local: Any, draft: GroupModerationDraft, member: Any) -> None:
    if draft.kind not in {"UNBAN", "UNMUTE"}:
        return
    rows = list(
        await local.session.scalars(
            select(GroupSanction).where(
                GroupSanction.chat_id == draft.chat_id,
                GroupSanction.telegram_id == draft.telegram_id,
                GroupSanction.kind.in_(("BAN", "KICK") if draft.kind == "UNBAN" else ("MUTE",)),
                GroupSanction.status.in_(LIVE),
            )
        )
    )
    if any(row.owned for row in rows):
        return
    if len(rows) == 1 and rows[0].status == "PENDING":
        return  # Existing UNBAN/UNMUTE cancels a known, not-yet-applied request.
    if any(row.status != "ACTIVE" for row in rows):
        raise DomainError(
            "group_moderation_uncertain"
            if any(r.status == "UNCERTAIN" for r in rows)
            else "group_moderation_busy"
        )
    observed = snapshot(member, draft.kind)
    if observed is None:
        return
    user = await Repository(local.session).user_by_telegram(draft.telegram_id)
    if user is not None and await Repository(local.session).has_scam_identity(user):
        raise DomainError("group_moderation_scam_protected")
    audit(
        local.session,
        "external_release_prepared",
        draft.actor_id,
        draft.chat_id,
        draft_id=draft.id,
        user_id=draft.telegram_id,
        kind=draft.kind,
        snapshot=observed,
        expected_sanction_id=rows[0].id if rows else None,
    )


async def confirm(local: Any, draft: GroupModerationDraft, event: AuditEvent) -> GroupSanction:
    await local._check_group(draft.chat_id)
    await local.authorization.actor(draft.chat_id, draft.actor_id)
    await local.authorization.bot_rights(draft.chat_id)
    member = await local.authorization.target(draft.chat_id, draft.telegram_id)
    try:
        await lock_group(local.session, draft.chat_id)
        await local.session.refresh(draft)
        if draft.status == "CONFIRMED" and draft.sanction_id:
            row = await local.session.get(GroupSanction, draft.sanction_id)
            assert row is not None
            await local.session.commit()
            return row
        if draft.status != "PENDING" or utc(draft.expires_at) <= now():
            raise DomainError("group_moderation_invalid_draft")
        user = await Repository(local.session).user_by_telegram(draft.telegram_id)
        if user is not None and await Repository(local.session).has_scam_identity(user):
            raise DomainError("group_moderation_scam_protected")
        if snapshot(member, draft.kind) != event.details.get("snapshot"):
            raise DomainError("group_moderation_restriction_changed")
        rows = list(
            await local.session.scalars(
                select(GroupSanction).where(
                    GroupSanction.chat_id == draft.chat_id,
                    GroupSanction.telegram_id == draft.telegram_id,
                    GroupSanction.kind.in_(("BAN", "KICK") if draft.kind == "UNBAN" else ("MUTE",)),
                    GroupSanction.status.in_(LIVE),
                )
            )
        )
        expected = event.details.get("expected_sanction_id")
        if len(rows) > 1 or (rows[0].id if rows else None) != expected:
            raise DomainError("group_moderation_restriction_changed")
        row = rows[0] if rows else None
        if row is not None and (row.owned or row.status != "ACTIVE"):
            raise DomainError("group_moderation_busy")
        if row is None:
            row = GroupSanction(
                chat_id=draft.chat_id,
                telegram_id=draft.telegram_id,
                kind="BAN" if draft.kind == "UNBAN" else "MUTE",
                actor_id=draft.actor_id,
                reason="",
                until_date=getattr(member, "until_date", None)
                if event.details["snapshot"]["until"]
                else None,
                status="ACTIVE",
                owned=False,
                policy_version=0,
                request_key="external:" + draft.id,
            )
            local.session.add(row)
            await local.session.flush()
        release = await local.session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == row.id,
                GroupSanctionJob.operation == "RELEASE",
            )
        )
        if release is not None and release.status not in {"FAILED", "CANCELLED", "PENDING"}:
            raise DomainError("group_moderation_busy")
        if release is None:
            release = GroupSanctionJob(sanction_id=row.id, operation="RELEASE")
            local.session.add(release)
        release.actor_id, release.automatic = draft.actor_id, False
        release.status, release.attempts, release.available_at = "PENDING", 0, now()
        release.lease_token, release.lease_until, release.result = None, None, None
        row.status, row.revoke_key, row.updated_at = "REVOKING", "local:" + draft.id, now()
        draft.status, draft.sanction_id = "CONFIRMED", row.id
        audit(
            local.session,
            "external_release_confirmed",
            draft.actor_id,
            draft.chat_id,
            draft_id=draft.id,
            sanction_id=row.id,
            user_id=draft.telegram_id,
            kind=draft.kind,
        )
        await local.session.commit()
        return row
    except Exception:
        await local.session.rollback()
        raise


async def authorization(session: Any, row: GroupSanction, job: GroupSanctionJob) -> dict | None:
    if job.automatic or not row.revoke_key or not row.revoke_key.startswith("local:"):
        return None
    draft = await session.get(GroupModerationDraft, row.revoke_key[6:], populate_existing=True)
    if draft is None or (
        draft.status,
        draft.sanction_id,
        draft.actor_id,
        draft.chat_id,
        draft.telegram_id,
    ) != ("CONFIRMED", row.id, job.actor_id, row.chat_id, row.telegram_id):
        return None
    if draft.kind != ("UNBAN" if row.kind in {"BAN", "KICK"} else "UNMUTE"):
        return None
    prepared = await intent(session, draft)
    confirmed = await session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.action == CONFIRMED,
            AuditEvent.actor_id == job.actor_id,
            AuditEvent.details["draft_id"].as_string() == draft.id,
            AuditEvent.details["sanction_id"].as_integer() == row.id,
        )
    )
    if prepared is None or confirmed is None:
        return None
    if utc(draft.expires_at) <= now():
        raise DomainError("group_moderation_invalid_draft")
    return prepared.details["snapshot"]

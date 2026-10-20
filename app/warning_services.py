"""Active warnings and escalation reuse the existing fenced sanction executor."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError
from app.models import (
    GroupModerationDraft,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    GroupWarningEscalation,
    GroupWarningReset,
    ManagedGroup,
    now,
)
from app.moderation_services import (
    LIVE,
    GroupModerationService,
    audit,
    lock_group,
    utc,
    validate_policy,
)

LIMIT = 3


def active_warnings(chat: int, target: int):
    return select(GroupSanction).where(
        GroupSanction.chat_id == chat,
        GroupSanction.telegram_id == target,
        GroupSanction.kind == "WARN",
        GroupSanction.status == "RECORDED",
    )


async def schedule_warning_mute(
    session: AsyncSession,
    warning: GroupSanction,
    policy: GroupModerationSettings,
    group: ManagedGroup,
) -> None:
    """Called under the existing group lock, in the WARN insertion transaction."""
    count = await session.scalar(
        select(func.count()).select_from(
            active_warnings(warning.chat_id, warning.telegram_id).subquery()
        )
    )
    if (count or 0) < LIMIT:
        return
    pending = await session.scalar(
        select(GroupWarningEscalation.id)
        .join(GroupSanction, GroupSanction.id == GroupWarningEscalation.sanction_id)
        .where(
            GroupSanction.status.in_(LIVE),
            GroupWarningEscalation.chat_id == warning.chat_id,
            GroupWarningEscalation.telegram_id == warning.telegram_id,
            GroupWarningEscalation.state.in_(("PENDING", "UNCERTAIN")),
        )
        .limit(1)
    )
    if pending is not None:
        return
    event = GroupWarningEscalation(
        chat_id=warning.chat_id, telegram_id=warning.telegram_id, trigger_id=warning.id
    )
    session.add(event)
    until = (now() + timedelta(days=1)).replace(microsecond=0)
    try:
        validate_policy(policy, group, "MUTE", until)
        existing = await session.scalar(
            select(GroupSanction.id)
            .where(
                GroupSanction.chat_id == warning.chat_id,
                GroupSanction.telegram_id == warning.telegram_id,
                GroupSanction.status.in_(LIVE),
            )
            .limit(1)
        )
        if existing is not None:
            raise DomainError("group_moderation_existing_restriction")
    except DomainError as error:
        event.state, event.result = "FAILED", error.code
        audit(
            session,
            "warning_escalation_blocked",
            warning.actor_id,
            warning.chat_id,
            user_id=warning.telegram_id,
            trigger_id=warning.id,
            result=error.code,
        )
        return
    key = "warn-auto:" + str(warning.id)
    sanction = GroupSanction(
        chat_id=warning.chat_id,
        telegram_id=warning.telegram_id,
        actor_id=warning.actor_id,
        kind="MUTE",
        reason="3 WARN",
        until_date=until,
        status="PENDING",
        owned=False,
        policy_version=policy.version,
        request_key=key,
    )
    session.add(sanction)
    await session.flush()
    from app.i18n import language

    draft = GroupModerationDraft(
        id=uuid4().hex,
        chat_id=warning.chat_id,
        telegram_id=warning.telegram_id,
        actor_id=warning.actor_id,
        kind="MUTE",
        reason="3 WARN",
        until_date=until,
        status="CONFIRMED",
        sanction_id=sanction.id,
        request_key=key,
        expires_at=now() + timedelta(minutes=10),
        language=language.get(),
    )
    session.add(draft)
    await session.flush()
    event.sanction_id, event.draft_id = sanction.id, draft.id
    session.add_all(
        [
            GroupSanctionJob(sanction_id=sanction.id, operation="APPLY", actor_id=warning.actor_id),
            GroupSanctionJob(
                sanction_id=sanction.id, operation="RELEASE", automatic=True, available_at=until
            ),
        ]
    )
    audit(
        session,
        "warning_escalation_requested",
        warning.actor_id,
        warning.chat_id,
        user_id=warning.telegram_id,
        trigger_id=warning.id,
        sanction_id=sanction.id,
    )


async def complete_warning_mute(
    session: AsyncSession, sanction: GroupSanction, job: GroupSanctionJob, state: str, result: str
) -> None:
    """Shares the executor's fenced completion transaction; never clears on failure."""
    if job.operation != "APPLY":
        return
    event = await session.scalar(
        select(GroupWarningEscalation).where(GroupWarningEscalation.sanction_id == sanction.id)
    )
    if event is None:
        return
    if state == "SUCCEEDED" and result == "APPLIED" and sanction.owned:
        event.state, event.result, event.updated_at = "SUCCEEDED", result, now()
        ids = list(
            await session.scalars(
                active_warnings(sanction.chat_id, sanction.telegram_id).with_only_columns(
                    GroupSanction.id
                )
            )
        )
        if ids:
            await session.execute(
                update(GroupSanction)
                .where(GroupSanction.id.in_(ids))
                .values(status="COMPLETED", updated_at=now())
            )
            await invalidate_warning_receipts(session, ids)
        audit(
            session,
            "warnings_consumed",
            sanction.actor_id,
            sanction.chat_id,
            user_id=sanction.telegram_id,
            sanction_id=sanction.id,
            warning_ids=ids,
        )
    elif state in {"FAILED", "CANCELLED", "UNCERTAIN"} or state == "SUCCEEDED":
        event.state = "FAILED" if state == "SUCCEEDED" else state
        event.result, event.updated_at = result, now()
        ids = list(
            await session.scalars(
                active_warnings(sanction.chat_id, sanction.telegram_id).with_only_columns(
                    GroupSanction.id
                )
            )
        )
        await invalidate_warning_receipts(session, ids)


async def invalidate_warning_receipts(session: AsyncSession, ids: list[int]) -> None:
    await session.execute(
        update(GroupModerationDraft)
        .where(GroupModerationDraft.sanction_id.in_(ids))
        .values(receipt_done=False, receipt_checked_at=None)
    )


async def reset_prompt(
    local: GroupModerationService, origin: GroupSanction, actor: int
) -> GroupWarningReset:
    await local._check_group(origin.chat_id)
    await local.authorization.actor(origin.chat_id, actor)
    await local.authorization.bot_rights(origin.chat_id)
    await local.authorization.target(origin.chat_id, origin.telegram_id)
    try:
        await lock_group(local.session, origin.chat_id)
        await local.session.refresh(origin)
        if origin.kind != "WARN" or origin.status != "RECORDED":
            raise DomainError("group_moderation_stale_control")
        ids = list(
            await local.session.scalars(
                active_warnings(origin.chat_id, origin.telegram_id).with_only_columns(
                    GroupSanction.id
                )
            )
        )
        if not ids:
            raise DomainError("group_moderation_stale_control")
        prompt = GroupWarningReset(
            id=uuid4().hex,
            origin_id=origin.id,
            actor_id=actor,
            max_warning_id=max(ids),
            expected_count=len(ids),
            expires_at=now() + timedelta(minutes=5),
        )
        local.session.add(prompt)
        await local.session.commit()
        return prompt
    except Exception:
        await local.session.rollback()
        raise


async def reset_warnings(local: GroupModerationService, token: str, actor: int) -> GroupSanction:
    prompt = await local.session.get(GroupWarningReset, token)
    if (
        prompt is None
        or prompt.actor_id != actor
        or prompt.state != "PENDING"
        or utc(prompt.expires_at) <= now()
    ):
        raise DomainError("group_moderation_stale_control")
    origin = await local.session.get(GroupSanction, prompt.origin_id)
    assert origin is not None
    await local._check_group(origin.chat_id)
    await local.authorization.actor(origin.chat_id, actor)
    await local.authorization.bot_rights(origin.chat_id)
    await local.authorization.target(origin.chat_id, origin.telegram_id)
    try:
        await lock_group(local.session, origin.chat_id)
        await local.session.refresh(prompt)
        ids = list(
            await local.session.scalars(
                active_warnings(origin.chat_id, origin.telegram_id).with_only_columns(
                    GroupSanction.id
                )
            )
        )
        if (
            prompt.state != "PENDING"
            or utc(prompt.expires_at) <= now()
            or len(ids) != prompt.expected_count
            or not ids
            or max(ids) != prompt.max_warning_id
        ):
            raise DomainError("group_moderation_stale_control")
        events = list(
            await local.session.scalars(
                select(GroupWarningEscalation).where(
                    GroupWarningEscalation.chat_id == origin.chat_id,
                    GroupWarningEscalation.telegram_id == origin.telegram_id,
                    GroupWarningEscalation.state.in_(("PENDING", "UNCERTAIN")),
                )
            )
        )
        for event in events:
            if event.sanction_id:
                sanction = await local.session.get(
                    GroupSanction, event.sanction_id, populate_existing=True
                )
                jobs = list(
                    await local.session.scalars(
                        select(GroupSanctionJob).where(
                            GroupSanctionJob.sanction_id == event.sanction_id
                        )
                    )
                )
                if (
                    sanction is None
                    or sanction.status != "PENDING"
                    or any(j.status in {"PROCESSING", "UNCERTAIN"} for j in jobs)
                ):
                    raise DomainError("group_moderation_busy")
                sanction.status, sanction.updated_at = "CANCELLED", now()
                for job in jobs:
                    if job.status == "PENDING":
                        job.status, job.result = "CANCELLED", "WARNINGS_CANCELLED"
            event.state, event.result, event.updated_at = "CANCELLED", "WARNINGS_CANCELLED", now()
        await local.session.execute(
            update(GroupSanction)
            .where(GroupSanction.id.in_(ids))
            .values(status="CANCELLED", updated_at=now())
        )
        await invalidate_warning_receipts(local.session, ids)
        prompt.state = "CONFIRMED"
        audit(
            local.session,
            "warnings_cancelled",
            actor,
            origin.chat_id,
            user_id=origin.telegram_id,
            warning_ids=ids,
            confirmation=token,
        )
        await local.session.commit()
        return origin
    except Exception:
        await local.session.rollback()
        raise


async def sync_warning_events(session: AsyncSession) -> None:
    """Record cancellation/recovery paths that finish before API execution."""
    events = list(
        await session.scalars(
            select(GroupWarningEscalation)
            .join(GroupSanction, GroupSanction.id == GroupWarningEscalation.sanction_id)
            .where(
                GroupWarningEscalation.state == "PENDING",
                GroupSanction.status.in_(("CANCELLED", "FAILED", "UNCERTAIN")),
            )
        )
    )
    for event in events:
        row = await session.get(GroupSanction, event.sanction_id, populate_existing=True)
        if row is not None and row.status in {"CANCELLED", "FAILED", "UNCERTAIN"}:
            event.state, event.result, event.updated_at = (
                row.status,
                "EXECUTOR_" + row.status,
                now(),
            )
            ids = list(
                await session.scalars(
                    active_warnings(row.chat_id, row.telegram_id).with_only_columns(
                        GroupSanction.id
                    )
                )
            )
            await invalidate_warning_receipts(session, ids)
    await session.commit()

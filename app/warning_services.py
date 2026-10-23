"""Active warnings and escalation reuse the existing fenced sanction executor."""

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
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


async def configure_warnings(
    local: GroupModerationService,
    chat: int,
    actor: int,
    *,
    expected_version: int,
    limit: int,
    action: str,
    mute_seconds: int,
) -> GroupModerationSettings:
    if (
        type(limit) is not int
        or not 2 <= limit <= 6
        or type(action) is not str
        or action not in {"OFF", "KICK", "MUTE", "BAN"}
        or type(mute_seconds) is not int
        or not 120 <= mute_seconds <= 31536000
        or type(expected_version) is not int
        or expected_version < 0
    ):
        raise DomainError("group_moderation_invalid_settings")
    await local._check_group(chat)
    await local.authorization.actor(chat, actor, configure=True)
    if action != "OFF":
        await local.authorization.bot_rights(chat)
    try:
        group = await lock_group(local.session, chat)
        policy = await local.session.get(GroupModerationSettings, chat, populate_existing=True)
        if (policy.warning_version if policy else 0) != expected_version:
            raise DomainError("group_moderation_request_conflict")
        if action in {"MUTE", "KICK"} and group.chat_type != "supergroup":
            raise DomainError("group_moderation_supergroup_required")
        if action == "MUTE" and mute_seconds > (policy.max_duration_seconds if policy else 2592000):
            raise DomainError("group_moderation_invalid_duration")
        if policy is None:
            policy = GroupModerationSettings(chat_id=chat)
            local.session.add(policy)
            await local.session.flush()
        before = (policy.warning_limit, policy.warning_action, policy.warning_mute_seconds)
        if before == (limit, action, mute_seconds) and expected_version != 0:
            await local.session.commit()
            return policy
        events = list(
            await local.session.scalars(
                select(GroupWarningEscalation).where(
                    GroupWarningEscalation.chat_id == chat,
                    GroupWarningEscalation.state.in_(("PENDING", "UNCERTAIN")),
                )
            )
        )
        for event in events:
            row = (
                await local.session.get(GroupSanction, event.sanction_id)
                if event.sanction_id
                else None
            )
            jobs = (
                list(
                    await local.session.scalars(
                        select(GroupSanctionJob).where(
                            GroupSanctionJob.sanction_id == event.sanction_id
                        )
                    )
                )
                if row
                else []
            )
            if event.state == "UNCERTAIN" or (
                row
                and (
                    row.status != "PENDING"
                    or any(j.status in {"PROCESSING", "UNCERTAIN"} for j in jobs)
                )
            ):
                raise DomainError("group_moderation_busy")
            if row:
                row.status, row.updated_at = "CANCELLED", now()
                for job in jobs:
                    if job.status == "PENDING":
                        job.status, job.result, job.updated_at = (
                            "CANCELLED",
                            "WARNING_POLICY_CHANGED",
                            now(),
                        )
            event.state, event.result, event.updated_at = (
                "CANCELLED",
                "WARNING_POLICY_CHANGED",
                now(),
            )
        policy.warning_limit, policy.warning_action, policy.warning_mute_seconds = (
            limit,
            action,
            mute_seconds,
        )
        policy.warning_version += 1
        policy.updated_by, policy.updated_at = actor, now()
        ids = list(
            await local.session.scalars(
                select(GroupSanction.id).where(
                    GroupSanction.chat_id == chat,
                    GroupSanction.kind == "WARN",
                    GroupSanction.status == "RECORDED",
                )
            )
        )
        await invalidate_warning_receipts(local.session, ids)
        audit(
            local.session,
            "warning_settings",
            actor,
            chat,
            previous={"limit": before[0], "action": before[1], "mute_seconds": before[2]},
            limit=limit,
            punishment=action,
            mute_seconds=mute_seconds,
            version=policy.warning_version,
        )
        await local.session.commit()
        return policy
    except Exception:
        await local.session.rollback()
        raise


def active_warnings(chat: int, target: int):
    return select(GroupSanction).where(
        GroupSanction.chat_id == chat,
        GroupSanction.telegram_id == target,
        GroupSanction.kind == "WARN",
        GroupSanction.status == "RECORDED",
    )


async def schedule_warning_action(
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
    if policy.warning_action == "OFF" or (count or 0) < policy.warning_limit:
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
        chat_id=warning.chat_id,
        telegram_id=warning.telegram_id,
        trigger_id=warning.id,
        warning_limit=policy.warning_limit,
    )
    session.add(event)
    kind = policy.warning_action
    until = (
        (now() + timedelta(seconds=policy.warning_mute_seconds)).replace(microsecond=0)
        if kind == "MUTE"
        else None
    )
    predecessor = None
    try:
        validate_policy(policy, group, kind, until)
        existing = list(
            await session.scalars(
                select(GroupSanction).where(
                    GroupSanction.chat_id == warning.chat_id,
                    GroupSanction.telegram_id == warning.telegram_id,
                    GroupSanction.status.in_(LIVE),
                )
            )
        )
        if existing:
            if (
                len(existing) != 1
                or kind not in {"BAN", "KICK"}
                or existing[0].kind != "MUTE"
                or existing[0].status != "ACTIVE"
            ):
                raise DomainError("group_moderation_existing_restriction")
            predecessor = existing[0]
            in_flight = await session.scalar(
                select(GroupSanctionJob.id)
                .where(
                    GroupSanctionJob.sanction_id == predecessor.id,
                    GroupSanctionJob.status.in_(("PROCESSING", "UNCERTAIN")),
                )
                .limit(1)
            )
            if in_flight is not None:
                raise DomainError("group_moderation_busy")
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
        kind=kind,
        reason=f"{policy.warning_limit} WARN",
        until_date=until,
        status="PENDING",
        owned=False,
        policy_version=policy.version,
        request_key=key,
        replaces_sanction_id=predecessor.id if predecessor else None,
    )
    session.add(sanction)
    await session.flush()
    from app.i18n import language

    draft = GroupModerationDraft(
        id=uuid4().hex,
        chat_id=warning.chat_id,
        telegram_id=warning.telegram_id,
        actor_id=warning.actor_id,
        kind=kind,
        reason=sanction.reason,
        until_date=until,
        status="CONFIRMED",
        sanction_id=sanction.id,
        request_key=key,
        expires_at=now() + timedelta(minutes=10),
        language=language.get(),
        receipt_visibility="PUBLIC",
    )
    session.add(draft)
    await session.flush()
    event.sanction_id, event.draft_id = sanction.id, draft.id
    session.add(
        GroupSanctionJob(sanction_id=sanction.id, operation="APPLY", actor_id=warning.actor_id)
    )
    if until or kind == "KICK":
        session.add(
            GroupSanctionJob(
                sanction_id=sanction.id,
                operation="RELEASE",
                automatic=True,
                available_at=until or now(),
            )
        )
    audit(
        session,
        "warning_escalation_requested",
        warning.actor_id,
        warning.chat_id,
        user_id=warning.telegram_id,
        trigger_id=warning.id,
        sanction_id=sanction.id,
        limit=policy.warning_limit,
        punishment=kind,
        mute_seconds=policy.warning_mute_seconds,
    )


async def complete_warning_action(
    session: AsyncSession, sanction: GroupSanction, job: GroupSanctionJob, state: str, result: str
) -> None:
    """Shares the executor's fenced completion transaction; never clears on failure."""
    if job.operation != ("RELEASE" if sanction.kind == "KICK" else "APPLY"):
        # A KICK is complete only after both ban and unban succeed.
        if (
            sanction.kind != "KICK"
            or job.operation != "APPLY"
            or state not in {"FAILED", "CANCELLED", "UNCERTAIN"}
        ):
            return
    if job.operation == "RELEASE" and sanction.kind != "KICK":
        return
    event = await session.scalar(
        select(GroupWarningEscalation).where(GroupWarningEscalation.sanction_id == sanction.id)
    )
    if event is None:
        return
    completed = (
        state == "SUCCEEDED"
        and sanction.owned
        and (
            result == "APPLIED"
            if sanction.kind != "KICK"
            else result in {"RELEASED", "ALREADY_CLEAR"}
        )
    )
    if completed:
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
    local: GroupModerationService,
    origin: GroupSanction,
    actor: int,
    *,
    selected_warning_id: int | None = None,
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
        if selected_warning_id is not None and selected_warning_id not in ids:
            raise DomainError("group_moderation_stale_control")
        prompt = GroupWarningReset(
            id=uuid4().hex,
            origin_id=origin.id,
            selected_warning_id=selected_warning_id,
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
        snapshot_ids = list(ids)
        if prompt.selected_warning_id is not None:
            if prompt.selected_warning_id not in ids:
                raise DomainError("group_moderation_stale_control")
            ids = [prompt.selected_warning_id]
        remaining_count = prompt.expected_count - len(ids)
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
            if remaining_count >= event.warning_limit:
                continue
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
        await invalidate_warning_receipts(local.session, snapshot_ids)
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
        public_origin = await local.session.scalar(
            select(GroupModerationDraft)
            .where(
                GroupModerationDraft.sanction_id.in_(ids),
                GroupModerationDraft.kind == "WARN",
                GroupModerationDraft.status == "CONFIRMED",
                GroupModerationDraft.panel_chat_id == origin.chat_id,
                GroupModerationDraft.panel_message_id.is_not(None),
                or_(
                    GroupModerationDraft.receipt_visibility == "PUBLIC",
                    GroupModerationDraft.receipt_visibility.is_(None),
                ),
            )
            .order_by(GroupModerationDraft.created_at.desc())
            .limit(1)
        )
        if public_origin is not None:
            from app.i18n import language

            # One durable summary for the confirmed reset, not one new message
            # per warning. The original warning receipts are updated as usual.
            local.session.add(
                GroupModerationDraft(
                    id=uuid4().hex,
                    chat_id=origin.chat_id,
                    actor_id=actor,
                    telegram_id=origin.telegram_id,
                    kind="WARN",
                    reason="",
                    status="CONFIRMED",
                    expires_at=now(),
                    sanction_id=ids[0],
                    request_key="warn-clear:" + token,
                    original_command="/unwarn",
                    language=language.get(),
                    receipt_visibility="PRIVATE",
                )
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

"""Local, opt-in group restrictions. Public commands/UI are intentionally not installed yet."""

import re
from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError
from app.group_authorization import GroupAuthorizer, numeric_id
from app.group_services import GroupService
from app.models import (
    AuditEvent,
    GroupModerationDraft,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    ManagedGroup,
    now,
)

LIVE = ("PENDING", "ACTIVE", "REVOKING", "UNCERTAIN")


async def has_local_restriction(
    session: AsyncSession, telegram_id: int, chat_ids: Collection[int]
) -> bool:
    """Global SCAM removal grants no authority to revoke a group's local sanction."""
    return bool(
        await session.scalar(
            select(
                select(GroupSanction.id)
                .where(
                    GroupSanction.telegram_id == telegram_id,
                    GroupSanction.chat_id.in_(chat_ids),
                    GroupSanction.status.in_(LIVE),
                )
                .exists()
            )
        )
    )


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def request_key(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{8,128}", value):
        raise DomainError("group_moderation_invalid_request")


def audit(session: AsyncSession, action: str, actor: int, chat_id: int, **details: Any) -> None:
    session.add(
        AuditEvent(
            actor_id=actor,
            action="group_moderation_" + action,
            details={"chat_id": chat_id, **details},
        )
    )


async def serialize_group(session: AsyncSession, chat_id: int) -> None:
    """Use the same lock order for completion/recovery even after a group is disabled."""
    await session.execute(
        update(ManagedGroup)
        .where(ManagedGroup.chat_id == chat_id)
        .values(updated_at=ManagedGroup.updated_at)
    )


def module_enabled(policy: GroupModerationSettings, kind: str) -> bool:
    return policy.enabled and bool(
        getattr(
            policy,
            {
                "BAN": "bans_enabled",
                "MUTE": "mutes_enabled",
                "WARN": "warnings_enabled",
                "KICK": "kicks_enabled",
            }.get(kind, ""),
            False,
        )
    )


async def lock_group(session: AsyncSession, chat_id: int) -> ManagedGroup:
    numeric_id(chat_id, chat=True)
    # Per-group serialization on both PostgreSQL and SQLite; no identity-wide lock.
    result = await session.execute(
        update(ManagedGroup)
        .where(
            ManagedGroup.chat_id == chat_id,
            ManagedGroup.approved.is_(True),
            ManagedGroup.enabled.is_(True),
            ~GroupService.removed_group(ManagedGroup.chat_id),
        )
        .values(updated_at=ManagedGroup.updated_at)
        .returning(ManagedGroup.chat_id)
    )
    if result.scalar_one_or_none() is None:
        raise DomainError("group_moderation_group_unavailable")
    group = await session.get(ManagedGroup, chat_id, populate_existing=True)
    assert group is not None
    return group


def validate_policy(
    policy: GroupModerationSettings | None,
    group: ManagedGroup,
    kind: str,
    until_date: datetime | None,
) -> None:
    if policy is None or not module_enabled(policy, kind):
        raise DomainError("group_moderation_disabled")
    if (kind in {"MUTE", "KICK"} or until_date is not None) and group.chat_type != "supergroup":
        raise DomainError("group_moderation_supergroup_required")
    if kind in {"WARN", "KICK"}:
        if until_date is not None:
            raise DomainError("group_moderation_invalid_duration")
    elif until_date is None:
        if not (
            (kind == "BAN" and policy.allow_permanent_bans)
            or (kind == "MUTE" and policy.allow_permanent_mutes)
        ):
            raise DomainError("group_moderation_invalid_duration")
    elif not 60 <= (until_date - now()).total_seconds() <= policy.max_duration_seconds:
        raise DomainError("group_moderation_invalid_duration")


class GroupModerationService:
    def __init__(self, session: AsyncSession, bot: Any):
        self.session, self.bot = session, bot
        self.authorization = GroupAuthorizer(bot)

    async def _check_group(self, chat_id: int) -> None:
        try:
            await lock_group(self.session, chat_id)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def configure(
        self,
        chat_id: int,
        actor_id: int,
        *,
        enabled: bool,
        expected_version: int | None = None,
        bans_enabled: bool = False,
        mutes_enabled: bool = False,
        max_duration_seconds: int = 2592000,
        allow_permanent_bans: bool = False,
        warnings_enabled: bool = False,
        kicks_enabled: bool = False,
        allow_permanent_mutes: bool = False,
    ) -> GroupModerationSettings:
        if (
            any(
                type(v) is not bool
                for v in (warnings_enabled, kicks_enabled, allow_permanent_mutes)
            )
            or type(enabled) is not bool
            or type(bans_enabled) is not bool
            or type(mutes_enabled) is not bool
            or type(allow_permanent_bans) is not bool
            or type(max_duration_seconds) is not int
            or not 60 <= max_duration_seconds <= 31536000
        ):
            raise DomainError("group_moderation_invalid_settings")
        await self._check_group(chat_id)
        await self.authorization.actor(chat_id, actor_id, configure=True)
        if enabled:
            await self.authorization.bot_rights(chat_id)
        try:
            await lock_group(self.session, chat_id)
            policy = await self.session.get(
                GroupModerationSettings, chat_id, populate_existing=True
            )
            if (
                expected_version is not None
                and (policy.version if policy else 0) != expected_version
            ):
                raise DomainError("group_moderation_request_conflict")
            if policy is None:
                policy = GroupModerationSettings(chat_id=chat_id, enabled=False, version=1)
                self.session.add(policy)
                await self.session.flush()
            values = (
                enabled,
                bans_enabled,
                mutes_enabled,
                max_duration_seconds,
                allow_permanent_bans,
                warnings_enabled,
                kicks_enabled,
                allow_permanent_mutes,
            )
            if values != (
                policy.enabled,
                policy.bans_enabled,
                policy.mutes_enabled,
                policy.max_duration_seconds,
                policy.allow_permanent_bans,
                policy.warnings_enabled,
                policy.kicks_enabled,
                policy.allow_permanent_mutes,
            ):
                (
                    policy.enabled,
                    policy.bans_enabled,
                    policy.mutes_enabled,
                    policy.max_duration_seconds,
                    policy.allow_permanent_bans,
                    policy.warnings_enabled,
                    policy.kicks_enabled,
                    policy.allow_permanent_mutes,
                ) = values
                policy.version += 1
                policy.updated_by, policy.updated_at = actor_id, now()
                pending = list(
                    await self.session.scalars(
                        select(GroupSanction).where(
                            GroupSanction.chat_id == chat_id, GroupSanction.status == "PENDING"
                        )
                    )
                )
                for sanction in pending:
                    # Never pretend an in-flight API request was cancelled.
                    apply = await self.session.scalar(
                        select(GroupSanctionJob).where(
                            GroupSanctionJob.sanction_id == sanction.id,
                            GroupSanctionJob.operation == "APPLY",
                        )
                    )
                    if apply is not None and apply.status == "PENDING":
                        sanction.status, sanction.updated_at = "CANCELLED", now()
                        await self.session.execute(
                            update(GroupSanctionJob)
                            .where(
                                GroupSanctionJob.sanction_id == sanction.id,
                                GroupSanctionJob.status == "PENDING",
                            )
                            .values(status="CANCELLED", result="POLICY_CHANGED")
                        )
                audit(
                    self.session,
                    "settings",
                    actor_id,
                    chat_id,
                    enabled=enabled,
                    bans_enabled=bans_enabled,
                    mutes_enabled=mutes_enabled,
                    version=policy.version,
                    max_duration_seconds=max_duration_seconds,
                    allow_permanent_bans=allow_permanent_bans,
                    warnings_enabled=warnings_enabled,
                    kicks_enabled=kicks_enabled,
                    allow_permanent_mutes=allow_permanent_mutes,
                )
            await self.session.commit()
            return policy
        except Exception:
            await self.session.rollback()
            raise

    async def submit(
        self,
        chat_id: int,
        actor_id: int,
        target_id: int,
        kind: str,
        *,
        until_date: datetime | None,
        reason: str,
        key: str,
        draft_id: str | None = None,
    ) -> GroupSanction:
        numeric_id(target_id)
        request_key(key)
        if (
            kind not in {"BAN", "MUTE", "WARN", "KICK"}
            or not isinstance(reason, str)
            or len(reason.strip()) > 1000
        ):
            raise DomainError("group_moderation_invalid_sanction")
        if until_date is not None and (
            not isinstance(until_date, datetime) or until_date.tzinfo is None
        ):
            raise DomainError("group_moderation_invalid_duration")
        until_date = until_date.astimezone(UTC).replace(microsecond=0) if until_date else None
        reason = reason.strip()
        await self._check_group(chat_id)
        await self.authorization.actor(chat_id, actor_id)
        await self.authorization.bot_rights(chat_id)
        await self.authorization.target(chat_id, target_id)
        try:
            group = await lock_group(self.session, chat_id)
            draft = await self.check_draft(draft_id, chat_id, actor_id, target_id, kind)
            if draft is not None and (
                key != "local:" + draft.id
                or reason != draft.reason
                or until_date != (utc(draft.until_date) if draft.until_date else None)
            ):
                raise DomainError("group_moderation_request_conflict")
            existing = await self.session.scalar(
                select(GroupSanction).where(GroupSanction.request_key == key)
            )
            if existing is not None:
                expected = (chat_id, actor_id, target_id, kind, reason, until_date)
                actual = (
                    existing.chat_id,
                    existing.actor_id,
                    existing.telegram_id,
                    existing.kind,
                    existing.reason,
                    utc(existing.until_date) if existing.until_date else None,
                )
                if actual != expected:
                    raise DomainError("group_moderation_request_conflict")
                if draft is not None and draft.sanction_id != existing.id:
                    raise DomainError("group_moderation_request_conflict")
                await self.session.commit()
                return existing
            policy = await self.session.get(
                GroupModerationSettings, chat_id, populate_existing=True
            )
            validate_policy(policy, group, kind, until_date)
            assert policy is not None
            active = await self.session.scalar(
                select(GroupSanction.id).where(
                    GroupSanction.chat_id == chat_id,
                    GroupSanction.telegram_id == target_id,
                    GroupSanction.status.in_(LIVE),
                )
            )
            if active is not None and kind != "WARN":
                raise DomainError("group_moderation_conflict")
            sanction = GroupSanction(
                chat_id=chat_id,
                telegram_id=target_id,
                kind=kind,
                actor_id=actor_id,
                reason=reason,
                until_date=until_date,
                policy_version=policy.version,
                request_key=key,
                status="RECORDED" if kind == "WARN" else "PENDING",
                owned=False,
            )
            self.session.add(sanction)
            await self.session.flush()
            if draft is not None:
                draft.status, draft.sanction_id = "CONFIRMED", sanction.id
            if kind != "WARN":
                self.session.add(
                    GroupSanctionJob(sanction_id=sanction.id, operation="APPLY", actor_id=actor_id)
                )
            if until_date or kind == "KICK":
                self.session.add(
                    GroupSanctionJob(
                        sanction_id=sanction.id,
                        operation="RELEASE",
                        automatic=True,
                        available_at=until_date or now(),
                    )
                )
            audit(
                self.session,
                "requested",
                actor_id,
                chat_id,
                user_id=target_id,
                sanction_id=sanction.id,
                kind=kind,
                request_key=key,
            )
            await self.session.commit()
            return sanction
        except IntegrityError as error:
            await self.session.rollback()
            raise DomainError("group_moderation_request_conflict") from error
        except Exception:
            await self.session.rollback()
            raise

    async def revoke(
        self,
        chat_id: int,
        actor_id: int,
        sanction_id: int,
        *,
        key: str,
        draft_id: str | None = None,
    ) -> GroupSanction:
        numeric_id(sanction_id)
        request_key(key)
        await self._check_group(chat_id)
        await self.authorization.actor(chat_id, actor_id)
        await self.authorization.bot_rights(chat_id)
        try:
            await lock_group(self.session, chat_id)
            sanction = await self.session.get(GroupSanction, sanction_id, populate_existing=True)
            if sanction is None or sanction.chat_id != chat_id:
                raise DomainError("group_moderation_not_found")
            draft = await self.check_draft(
                draft_id,
                chat_id,
                actor_id,
                sanction.telegram_id,
                "UNBAN" if sanction.kind in {"BAN", "KICK"} else "UNMUTE",
            )
            if draft is not None and key != "local:" + draft.id:
                raise DomainError("group_moderation_request_conflict")
            if draft is not None and draft.status == "CONFIRMED":
                if draft.sanction_id != sanction.id:
                    raise DomainError("group_moderation_request_conflict")
                await self.session.commit()
                return sanction
            if sanction.revoke_key is not None:
                release = await self.session.scalar(
                    select(GroupSanctionJob).where(
                        GroupSanctionJob.sanction_id == sanction.id,
                        GroupSanctionJob.operation == "RELEASE",
                    )
                )
                retry = (
                    draft is not None
                    and draft.status == "PENDING"
                    and sanction.status == "ACTIVE"
                    and release is not None
                    and release.status in {"FAILED", "CANCELLED"}
                )
                if not retry:
                    if sanction.revoke_key != key or (
                        release is not None and release.actor_id != actor_id
                    ):
                        raise DomainError("group_moderation_request_conflict")
                    await self.session.commit()
                    return sanction
            if sanction.status in {"UNCERTAIN", "REVOKING"}:
                raise DomainError("group_moderation_uncertain")
            processing = await self.session.scalar(
                select(GroupSanctionJob.id)
                .where(
                    GroupSanctionJob.sanction_id == sanction.id,
                    GroupSanctionJob.status == "PROCESSING",
                )
                .limit(1)
            )
            if processing is not None:
                raise DomainError("group_moderation_busy")
            sanction.revoke_key, sanction.updated_at = key, now()
            if draft is not None:
                draft.status, draft.sanction_id = "CONFIRMED", sanction.id
            release = await self.session.scalar(
                select(GroupSanctionJob).where(
                    GroupSanctionJob.sanction_id == sanction.id,
                    GroupSanctionJob.operation == "RELEASE",
                )
            )
            if sanction.status == "ACTIVE":
                sanction.status = "REVOKING"
                if release is None:
                    release = GroupSanctionJob(sanction_id=sanction.id, operation="RELEASE")
                    self.session.add(release)
                release.actor_id, release.automatic, release.available_at = actor_id, False, now()
                release.status, release.attempts = "PENDING", 0
            else:
                sanction.status = "REVOKED"
                await self.session.execute(
                    update(GroupSanctionJob)
                    .where(
                        GroupSanctionJob.sanction_id == sanction.id,
                        GroupSanctionJob.status == "PENDING",
                    )
                    .values(status="CANCELLED", result="REVOKED_BEFORE_APPLY")
                )
                # Retain the revoker even for a no-API revocation.
                if release is None:
                    self.session.add(
                        GroupSanctionJob(
                            sanction_id=sanction.id,
                            operation="RELEASE",
                            actor_id=actor_id,
                            status="SUCCEEDED",
                            result="NOT_APPLIED",
                        )
                    )
                else:
                    release.actor_id = actor_id
            audit(
                self.session,
                "revoked",
                actor_id,
                chat_id,
                user_id=sanction.telegram_id,
                sanction_id=sanction.id,
                request_key=key,
            )
            await self.session.commit()
            return sanction
        except IntegrityError as error:
            await self.session.rollback()
            raise DomainError("group_moderation_request_conflict") from error
        except Exception:
            await self.session.rollback()
            raise

    async def check_draft(
        self, draft_id: str | None, chat_id: int, actor_id: int, target_id: int, kind: str
    ) -> GroupModerationDraft | None:
        if draft_id is None:
            return None
        draft = await self.session.get(GroupModerationDraft, draft_id, populate_existing=True)
        if (
            draft is None
            or (draft.chat_id, draft.actor_id, draft.telegram_id, draft.kind)
            != (chat_id, actor_id, target_id, kind)
            or draft.status == "CANCELLED"
            or (draft.status == "PENDING" and utc(draft.expires_at) <= now())
        ):
            raise DomainError("group_moderation_invalid_draft")
        return draft

    async def prepare(
        self,
        chat_id: int,
        actor_id: int,
        target_id: int,
        kind: str,
        *,
        until_date: datetime | None,
        reason: str,
        request: str,
    ) -> GroupModerationDraft:
        from datetime import timedelta
        from uuid import uuid4

        numeric_id(target_id)
        request_key(request)
        if (
            kind not in {"BAN", "MUTE", "WARN", "KICK", "UNBAN", "UNMUTE"}
            or not isinstance(reason, str)
            or len(reason) > 1000
        ):
            raise DomainError("group_moderation_invalid_sanction")
        if until_date is not None and (
            not isinstance(until_date, datetime) or until_date.tzinfo is None
        ):
            raise DomainError("group_moderation_invalid_duration")
        reason = reason.strip()
        await self._check_group(chat_id)
        await self.authorization.actor(chat_id, actor_id)
        await self.authorization.bot_rights(chat_id)
        await self.authorization.target(chat_id, target_id)
        try:
            group = await lock_group(self.session, chat_id)
            if kind not in {"UNBAN", "UNMUTE"}:
                policy = await self.session.get(
                    GroupModerationSettings, chat_id, populate_existing=True
                )
                validate_policy(policy, group, kind, until_date)
            existing = await self.session.scalar(
                select(GroupModerationDraft).where(GroupModerationDraft.request_key == request)
            )
            until_date = utc(until_date).replace(microsecond=0) if until_date else None
            if existing is not None:
                if (
                    existing.chat_id,
                    existing.actor_id,
                    existing.telegram_id,
                    existing.kind,
                    existing.reason,
                    utc(existing.until_date) if existing.until_date else None,
                ) != (chat_id, actor_id, target_id, kind, reason, until_date):
                    raise DomainError("group_moderation_request_conflict")
                await self.session.commit()
                return existing
            draft = GroupModerationDraft(
                id=uuid4().hex,
                chat_id=chat_id,
                actor_id=actor_id,
                telegram_id=target_id,
                kind=kind,
                until_date=until_date,
                reason=reason,
                request_key=request,
                expires_at=now() + timedelta(minutes=10),
                status="PENDING",
            )
            self.session.add(draft)
            await self.session.commit()
            return draft
        except Exception:
            await self.session.rollback()
            raise

    async def cancel_draft(self, draft_id: str, actor_id: int) -> GroupModerationDraft:
        draft = await self.session.get(GroupModerationDraft, draft_id)
        if draft is None or draft.actor_id != actor_id:
            raise DomainError("group_moderation_invalid_draft")
        chat_id = draft.chat_id
        await self._check_group(chat_id)
        await self.authorization.actor(chat_id, actor_id)
        try:
            await lock_group(self.session, chat_id)
            await self.session.refresh(draft)
            if draft.status == "PENDING":
                draft.status = "CANCELLED"
                audit(self.session, "draft_cancelled", actor_id, chat_id, draft_id=draft.id)
            elif draft.status == "CONFIRMED":
                raise DomainError("group_moderation_busy")
            await self.session.commit()
            return draft
        except Exception:
            await self.session.rollback()
            raise

    async def confirm(self, draft_id: str, actor_id: int) -> GroupSanction:
        from app.repositories import Repository

        draft = await self.session.get(GroupModerationDraft, draft_id, populate_existing=True)
        if draft is None or draft.actor_id != actor_id or draft.status == "CANCELLED":
            raise DomainError("group_moderation_invalid_draft")
        if draft.status == "PENDING" and utc(draft.expires_at) <= now():
            raise DomainError("group_moderation_invalid_draft")
        chat, target, kind, reason, until = (
            draft.chat_id,
            draft.telegram_id,
            draft.kind,
            draft.reason,
            draft.until_date,
        )
        await self.session.commit()
        if kind not in {"UNBAN", "UNMUTE"}:
            return await self.submit(
                chat,
                actor_id,
                target,
                kind,
                until_date=utc(until) if until else None,
                reason=reason,
                key="local:" + draft_id,
                draft_id=draft_id,
            )
        user = await Repository(self.session).user_by_telegram(target)
        if user is not None and await Repository(self.session).has_scam_identity(user):
            raise DomainError("group_moderation_scam_protected")
        sanction = await self.session.scalar(
            select(GroupSanction)
            .where(
                GroupSanction.chat_id == chat,
                GroupSanction.telegram_id == target,
                GroupSanction.kind.in_(("BAN", "KICK") if kind == "UNBAN" else ("MUTE",)),
                GroupSanction.status.in_(LIVE),
            )
            .order_by(GroupSanction.id.desc())
            .limit(1)
        )
        # Repeated confirmation must still resolve the original completed revocation.
        if draft.status == "CONFIRMED" and draft.sanction_id:
            sanction = await self.session.get(GroupSanction, draft.sanction_id)
        if sanction is None:
            raise DomainError("group_moderation_not_found")
        return await self.revoke(
            chat, actor_id, sanction.id, key="local:" + draft_id, draft_id=draft_id
        )

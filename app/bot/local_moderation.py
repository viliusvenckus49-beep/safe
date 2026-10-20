"""Fenced native Bot API executor for opt-in local sanctions; never uses Group Help."""

import asyncio
from datetime import timedelta
from typing import Any
from uuid import uuid4

import structlog
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ChatPermissions
from sqlalchemy import or_, select, true, update

from app.bot.moderation_lock import user_moderation_lock
from app.errors import DomainError, safe_telegram_reason
from app.group_authorization import GroupAuthorizer
from app.models import GroupModerationSettings, GroupSanction, GroupSanctionJob, now
from app.moderation_services import audit, lock_group, module_enabled, serialize_group, utc
from app.repositories import Repository

LEASE_SECONDS = 60
API_TIMEOUT = 8
MAX_ATTEMPTS = 5
MUTE_PERMISSIONS = ChatPermissions(
    **{key: False for key in ChatPermissions.model_fields if key.startswith("can_")}
)
UNMUTE_PERMISSIONS = ChatPermissions(
    **{key: True for key in ChatPermissions.model_fields if key.startswith("can_")}
)


def deadline(value: Any) -> int | None:
    return int(utc(value).timestamp()) if value is not None and value.timestamp() > 0 else None


def matches_group_defaults(member: Any, permissions: Any) -> bool:
    """A permanent default-shaped exception is not an additional restriction.

    Older releases copied group defaults into the member's personal permissions.
    Unknown fields, a deadline or a departed member must never qualify.
    """
    return (
        isinstance(permissions, ChatPermissions)
        and getattr(member, "is_member", None) is True
        and deadline(getattr(member, "until_date", None)) is None
        and all(
            type(getattr(member, key, None)) is bool
            and getattr(member, key) is (getattr(permissions, key) is True)
            for key in MUTE_PERMISSIONS.model_dump(exclude_none=True)
        )
    )


async def cancel_unused_releases(session: Any, sanction: GroupSanction, actor: int) -> None:
    """Definitively unowned terminal APPLY paths have nothing to release."""
    if sanction.owned or sanction.status not in {"FAILED", "CANCELLED"}:
        return
    abandoned = list(
        await session.scalars(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == sanction.id,
                GroupSanctionJob.operation == "RELEASE",
                GroupSanctionJob.status == "PENDING",
                GroupSanctionJob.attempts == 0,
                GroupSanctionJob.lease_token.is_(None),
                GroupSanctionJob.lease_until.is_(None),
            )
        )
    )
    for pending in abandoned:
        pending.status, pending.result, pending.updated_at = (
            "CANCELLED",
            "NO_CONFIRMED_RESTRICTION",
            now(),
        )
        audit(
            session,
            "orphan_release_cancelled",
            actor,
            sanction.chat_id,
            sanction_id=sanction.id,
            job_id=pending.id,
            result=pending.result,
        )


class LocalModerationExecutor:
    def __init__(self, sessions: Any, bot: Any):
        self.sessions, self.bot = sessions, bot
        self.authorization = GroupAuthorizer(bot)

    async def recover(self) -> int:
        """An expired write lease has an unknown outcome, not permission to replay it."""
        recovered = 0
        async with self.sessions() as session:
            ids = list(
                await session.scalars(
                    select(GroupSanctionJob.id)
                    .where(
                        GroupSanctionJob.status == "PROCESSING",
                        GroupSanctionJob.lease_until <= now(),
                    )
                    .limit(100)
                )
            )
            await session.commit()
            for job_id in ids:
                job = await session.get(GroupSanctionJob, job_id, populate_existing=True)
                if job is None:
                    continue
                sanction = await session.get(GroupSanction, job.sanction_id)
                assert sanction is not None
                await serialize_group(session, sanction.chat_id)
                # A CAS and lease fence make recovery safe when another worker just completed.
                changed = await session.execute(
                    update(GroupSanctionJob)
                    .execution_options(synchronize_session=False)
                    .where(
                        GroupSanctionJob.id == job_id,
                        GroupSanctionJob.status == "PROCESSING",
                        GroupSanctionJob.lease_until <= now(),
                    )
                    .values(
                        status="UNCERTAIN",
                        result="LEASE_EXPIRED",
                        lease_token=None,
                        lease_until=None,
                    )
                )
                if changed.rowcount:
                    sanction.status, sanction.updated_at = "UNCERTAIN", now()
                    audit(
                        session,
                        "uncertain",
                        job.actor_id or 0,
                        sanction.chat_id,
                        user_id=sanction.telegram_id,
                        sanction_id=sanction.id,
                        job_id=job.id,
                        result="LEASE_EXPIRED",
                    )
                    recovered += 1
                await session.commit()
        return recovered

    async def claim(self, sanction_id: int | None = None) -> tuple[int, str] | None:
        async with self.sessions() as session:
            candidates = list(
                await session.scalars(
                    select(GroupSanctionJob.id)
                    .join(GroupSanction, GroupSanction.id == GroupSanctionJob.sanction_id)
                    .where(
                        GroupSanctionJob.status == "PENDING",
                        GroupSanctionJob.sanction_id == sanction_id
                        if sanction_id is not None
                        else true(),
                        GroupSanctionJob.available_at <= now(),
                        GroupSanctionJob.attempts < MAX_ATTEMPTS,
                        or_(
                            GroupSanctionJob.operation == "APPLY",
                            GroupSanction.status != "PENDING",
                        ),
                    )
                    .order_by(GroupSanctionJob.available_at, GroupSanctionJob.id)
                    .limit(20)
                )
            )
            await session.commit()
            for job_id in candidates:
                job = await session.get(GroupSanctionJob, job_id, populate_existing=True)
                if job is None:
                    continue
                sanction = await session.get(GroupSanction, job.sanction_id, populate_existing=True)
                assert sanction is not None
                try:
                    await lock_group(session, sanction.chat_id)
                except DomainError:
                    await session.rollback()
                    await self._cancel_unclaimed(job_id, "GROUP_UNAVAILABLE")
                    continue
                await session.refresh(job)
                await session.refresh(sanction)
                if job.status != "PENDING" or utc(job.available_at) > now():
                    await session.commit()
                    continue
                if job.operation == "APPLY":
                    policy = await session.get(
                        GroupModerationSettings, sanction.chat_id, populate_existing=True
                    )
                    valid = (
                        sanction.status == "PENDING"
                        and policy is not None
                        and module_enabled(policy, sanction.kind)
                        and policy.version == sanction.policy_version
                    )
                    if not valid or (
                        sanction.until_date
                        and (utc(sanction.until_date) - now()).total_seconds() < 60
                    ):
                        job.status, job.result = "CANCELLED", "POLICY_OR_DEADLINE_CHANGED"
                        if sanction.status == "PENDING":
                            sanction.status = "CANCELLED"
                        sanction.updated_at = now()
                        audit(
                            session,
                            "cancelled",
                            job.actor_id or 0,
                            sanction.chat_id,
                            sanction_id=sanction.id,
                            job_id=job.id,
                            result=job.result,
                        )
                        await cancel_unused_releases(session, sanction, job.actor_id or 0)
                        await session.commit()
                        continue
                elif sanction.status not in {"ACTIVE", "REVOKING"}:
                    if sanction.status == "PENDING":
                        # Apply may be waiting on a Telegram flood limit; never release it early.
                        await session.commit()
                        continue
                    job.status, job.result = "CANCELLED", "NO_CONFIRMED_RESTRICTION"
                    sanction.updated_at = now()
                    await session.commit()
                    continue
                elif (
                    job.automatic
                    and sanction.until_date is not None
                    and utc(sanction.until_date) > now()
                ):
                    # Queue availability is not authority to lift an unexpired sanction.
                    job.available_at = sanction.until_date
                    await session.commit()
                    continue
                if job.operation == "RELEASE" and await session.scalar(
                    select(GroupSanction.id)
                    .where(
                        GroupSanction.replaces_sanction_id == sanction.id,
                        GroupSanction.status.in_({"PENDING", "ACTIVE", "REVOKING", "UNCERTAIN"}),
                    )
                    .limit(1)
                ):
                    # Never lift a MUTE while a linked BAN/KICK can still take effect.
                    await session.commit()
                    continue
                token = uuid4().hex
                changed = await session.execute(
                    update(GroupSanctionJob)
                    .execution_options(synchronize_session=False)
                    .where(
                        GroupSanctionJob.id == job_id,
                        GroupSanctionJob.status == "PENDING",
                        GroupSanctionJob.attempts < MAX_ATTEMPTS,
                    )
                    .values(
                        status="PROCESSING",
                        attempts=GroupSanctionJob.attempts + 1,
                        lease_token=token,
                        lease_until=now() + timedelta(seconds=LEASE_SECONDS),
                        result="CLAIMED",
                        updated_at=now(),
                    )
                )
                await session.commit()
                if changed.rowcount:
                    return job_id, token
        return None

    async def _cancel_unclaimed(self, job_id: int, result: str) -> None:
        async with self.sessions() as session:
            initial = await session.get(GroupSanctionJob, job_id)
            if initial is None:
                return
            sanction = await session.get(GroupSanction, initial.sanction_id)
            assert sanction is not None
            await serialize_group(session, sanction.chat_id)
            await session.refresh(sanction)
            changed = await session.execute(
                update(GroupSanctionJob)
                .execution_options(synchronize_session=False)
                .where(GroupSanctionJob.id == job_id, GroupSanctionJob.status == "PENDING")
                .values(status="CANCELLED", result=result)
            )
            if changed.rowcount:
                job = await session.get(GroupSanctionJob, job_id)
                assert job is not None
                sanction = await session.get(GroupSanction, job.sanction_id)
                assert sanction is not None
                if sanction.status == "PENDING":
                    sanction.status = "CANCELLED"
                elif sanction.status == "REVOKING":
                    sanction.status = "ACTIVE"
                sanction.updated_at = now()
                audit(
                    session,
                    "cancelled",
                    job.actor_id or 0,
                    sanction.chat_id,
                    sanction_id=sanction.id,
                    job_id=job.id,
                    result=result,
                )
                if job.operation == "APPLY":
                    await cancel_unused_releases(session, sanction, job.actor_id or 0)
            await session.commit()

    async def execute(self, job_id: int, token: str) -> None:
        async with self.sessions() as session:
            job = await session.get(GroupSanctionJob, job_id)
            if job is None:
                return
            sanction = await session.get(GroupSanction, job.sanction_id)
            assert sanction is not None
            user_id = sanction.telegram_id
            await session.commit()
        # Also serialize against the existing SCAM engine in the polling process.
        async with user_moderation_lock(user_id):
            await self._execute_locked(job_id, token)

    async def _execute_locked(self, job_id: int, token: str) -> None:
        sent = False
        async with self.sessions() as session:
            job = await session.get(GroupSanctionJob, job_id)
            if (
                job is None
                or job.status != "PROCESSING"
                or job.lease_token != token
                or job.lease_until is None
                or utc(job.lease_until) <= now()
                or job.result != "CLAIMED"
            ):
                return
            sanction = await session.get(GroupSanction, job.sanction_id)
            assert sanction is not None
            try:
                await lock_group(session, sanction.chat_id)
                # A claim token is also consumed exactly once. asyncio locks do not
                # protect duplicate delivery to independent executor processes.
                started = await session.execute(
                    update(GroupSanctionJob)
                    .execution_options(synchronize_session=False)
                    .where(
                        GroupSanctionJob.id == job_id,
                        GroupSanctionJob.status == "PROCESSING",
                        GroupSanctionJob.lease_token == token,
                        GroupSanctionJob.lease_until > now(),
                        GroupSanctionJob.result == "CLAIMED",
                    )
                    .values(result="EXECUTING")
                )
                if not started.rowcount:
                    await session.rollback()
                    return
                if job.automatic and (
                    job.operation != "RELEASE"
                    or (
                        sanction.kind != "KICK"
                        and (sanction.until_date is None or utc(sanction.until_date) > now())
                    )
                    or (sanction.kind == "KICK" and not sanction.owned)
                ):
                    raise DomainError("group_moderation_invalid_automatic_job")
                if job.operation == "APPLY":
                    policy = await session.get(
                        GroupModerationSettings, sanction.chat_id, populate_existing=True
                    )
                    if (
                        policy is None
                        or not module_enabled(policy, sanction.kind)
                        or policy.version != sanction.policy_version
                        or sanction.status != "PENDING"
                    ):
                        raise DomainError("group_moderation_policy_changed")
                    if (
                        sanction.until_date
                        and (utc(sanction.until_date) - now()).total_seconds() < 60
                    ):
                        raise DomainError("group_moderation_deadline_changed")
                elif sanction.status not in {"ACTIVE", "REVOKING"}:
                    raise DomainError("group_moderation_stale_job")
                await session.commit()  # Never retain SQL locks across Telegram requests.
                if not job.automatic:
                    await self.authorization.actor(sanction.chat_id, job.actor_id)
                await self.authorization.bot_rights(sanction.chat_id)
                member = await self.authorization.target(sanction.chat_id, sanction.telegram_id)
                if (
                    job.operation == "APPLY"
                    and sanction.kind == "KICK"
                    and (
                        member.status == "left"
                        or member.status == "restricted"
                        and getattr(member, "is_member", None) is False
                    )
                ):
                    raise DomainError("group_moderation_target_not_member")
                group = await lock_group(session, sanction.chat_id)
                await session.refresh(job)
                await session.refresh(sanction)
                if (
                    job.status != "PROCESSING"
                    or job.lease_token != token
                    or utc(job.lease_until) <= now()
                ):
                    await session.rollback()
                    return
                if job.operation == "APPLY":
                    policy = await session.get(
                        GroupModerationSettings, sanction.chat_id, populate_existing=True
                    )
                    if (
                        policy is None
                        or not module_enabled(policy, sanction.kind)
                        or policy.version != sanction.policy_version
                    ):
                        raise DomainError("group_moderation_policy_changed")
                    if (
                        sanction.kind in {"MUTE", "KICK"} or sanction.until_date is not None
                    ) and group.chat_type != "supergroup":
                        raise DomainError("group_moderation_supergroup_required")
                user = await Repository(session).user_by_telegram(sanction.telegram_id)
                scam = user is not None and await Repository(session).has_scam_identity(user)
                if job.operation == "APPLY" and scam:
                    raise DomainError("group_moderation_scam_protected")
                if job.operation == "RELEASE":
                    if scam or not sanction.owned:
                        await self.finish(
                            session,
                            job,
                            sanction,
                            token,
                            "SUCCEEDED",
                            "SCAM_PROTECTED" if scam else "NOT_OWNED",
                        )
                        return
                await session.commit()
                until = utc(sanction.until_date) if sanction.until_date else None
                result, owned = "APPLIED", sanction.owned
                if job.operation == "APPLY":
                    if member.status == "kicked":
                        if sanction.replaces_sanction_id is not None:
                            # A separate ban appeared after the MUTE snapshot.
                            # Do not adopt it as the success of this replacement.
                            raise DomainError("group_moderation_existing_restriction")
                        if sanction.kind != "BAN":
                            raise DomainError("group_moderation_existing_restriction")
                        existing_until = deadline(getattr(member, "until_date", None))
                        wanted_until = deadline(until)
                        if existing_until is not None and (
                            wanted_until is None or existing_until < wanted_until
                        ):
                            # Preserve foreign restrictions without claiming that a
                            # shorter ban fulfilled this request's longer duration.
                            raise DomainError("group_moderation_existing_restriction")
                        result, owned = "ALREADY_BANNED", False
                    else:
                        if member.status == "restricted":
                            predecessor = (
                                await session.get(GroupSanction, sanction.replaces_sanction_id)
                                if sanction.replaces_sanction_id
                                else None
                            )
                            owned_mute = (
                                predecessor is not None
                                and sanction.kind in {"BAN", "KICK"}
                                and predecessor.chat_id == sanction.chat_id
                                and predecessor.telegram_id == sanction.telegram_id
                                and predecessor.kind == "MUTE"
                                and predecessor.status == "ACTIVE"
                                and predecessor.owned
                                and getattr(member, "is_member", None) is True
                                and deadline(getattr(member, "until_date", None))
                                == deadline(predecessor.until_date)
                                and all(
                                    getattr(member, key, None) is False
                                    for key in MUTE_PERMISSIONS.model_dump(exclude_none=True)
                                )
                            )
                            defaults_match = False
                            if not owned_mute:
                                chat = await asyncio.wait_for(
                                    self.bot.get_chat(sanction.chat_id), API_TIMEOUT
                                )
                                defaults_match = matches_group_defaults(
                                    member, getattr(chat, "permissions", None)
                                )
                            if not owned_mute and not defaults_match:
                                raise DomainError("group_moderation_existing_restriction")
                        self.check_write_deadline(job, sanction)
                        sent = True
                        if sanction.kind in {"BAN", "KICK"}:
                            success = await asyncio.wait_for(
                                self.bot.ban_chat_member(
                                    chat_id=sanction.chat_id,
                                    user_id=sanction.telegram_id,
                                    until_date=until,
                                ),
                                API_TIMEOUT,
                            )
                        else:
                            success = await asyncio.wait_for(
                                self.bot.restrict_chat_member(
                                    chat_id=sanction.chat_id,
                                    user_id=sanction.telegram_id,
                                    permissions=MUTE_PERMISSIONS,
                                    until_date=until,
                                    use_independent_chat_permissions=True,
                                ),
                                API_TIMEOUT,
                            )
                        if success is not True:
                            await self.finish(session, job, sanction, token, "FAILED", "API_FALSE")
                            return
                        owned = True
                else:
                    expected = "kicked" if sanction.kind in {"BAN", "KICK"} else "restricted"
                    if member.status != expected:
                        result = "ALREADY_CLEAR"
                    elif deadline(getattr(member, "until_date", None)) != deadline(until):
                        result = "RESTRICTION_CHANGED"
                    elif sanction.kind == "MUTE" and any(
                        getattr(member, key, None) is not False
                        for key in MUTE_PERMISSIONS.model_dump(exclude_none=True)
                    ):
                        result = "RESTRICTION_CHANGED"
                    elif sanction.kind in {"BAN", "KICK"}:
                        self.check_write_deadline(job, sanction)
                        sent = True
                        success = await asyncio.wait_for(
                            self.bot.unban_chat_member(
                                chat_id=sanction.chat_id,
                                user_id=sanction.telegram_id,
                                only_if_banned=True,
                            ),
                            API_TIMEOUT,
                        )
                        if success is not True:
                            await self.finish(session, job, sanction, token, "FAILED", "API_FALSE")
                            return
                        result = "RELEASED"
                    else:
                        self.check_write_deadline(job, sanction)
                        sent = True
                        success = await asyncio.wait_for(
                            self.bot.restrict_chat_member(
                                chat_id=sanction.chat_id,
                                user_id=sanction.telegram_id,
                                # Bot API: all True removes the personal restriction.
                                # Group-wide default permissions still apply.
                                permissions=UNMUTE_PERMISSIONS,
                                use_independent_chat_permissions=True,
                            ),
                            API_TIMEOUT,
                        )
                        if success is not True:
                            await self.finish(session, job, sanction, token, "FAILED", "API_FALSE")
                            return
                        result = "RELEASED"
                await self.finish(session, job, sanction, token, "SUCCEEDED", result, owned=owned)
            except TelegramRetryAfter as error:
                await session.rollback()
                await self.finish(
                    session,
                    job,
                    sanction,
                    token,
                    "PENDING",
                    "TelegramRetryAfter",
                    delay=error.retry_after,
                    reason=safe_telegram_reason(error.message),
                )
            except DomainError as error:
                await session.rollback()
                await self.finish(session, job, sanction, token, "CANCELLED", error.code)
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                await session.rollback()
                await self.finish(
                    session,
                    job,
                    sanction,
                    token,
                    "FAILED",
                    type(error).__name__,
                    reason=safe_telegram_reason(error.message),
                )
            except (TelegramAPIError, TimeoutError, OSError) as error:
                await session.rollback()
                await self.finish(
                    session,
                    job,
                    sanction,
                    token,
                    "UNCERTAIN" if sent else "PENDING",
                    type(error).__name__,
                    delay=30,
                    reason=safe_telegram_reason(error.message)
                    if isinstance(error, TelegramAPIError)
                    else None,
                )

    @staticmethod
    def check_write_deadline(job: GroupSanctionJob, sanction: GroupSanction) -> None:
        # Recheck AFTER rights/SQL/API reads. Telegram treats expiry <30s as permanent.
        instant = now()
        if (
            job.lease_until is None
            or (utc(job.lease_until) - instant).total_seconds() <= API_TIMEOUT + 1
        ):
            raise DomainError("group_moderation_lease_expiring")
        if (
            job.operation == "APPLY"
            and sanction.until_date is not None
            and (utc(sanction.until_date) - instant).total_seconds() < 60
        ):
            raise DomainError("group_moderation_deadline_changed")

    async def finish(
        self,
        session: Any,
        job: GroupSanctionJob,
        sanction: GroupSanction,
        token: str,
        state: str,
        result: str,
        *,
        delay: int = 0,
        owned: bool | None = None,
        reason: str | None = None,
    ) -> None:
        # Rollback expires ORM fields, so use the stable identity rather than implicit async IO.
        from sqlalchemy import inspect

        job_identity, sanction_identity = inspect(job).identity, inspect(sanction).identity
        assert job_identity is not None and sanction_identity is not None
        job_id, sanction_id = job_identity[0], sanction_identity[0]
        await session.refresh(sanction)
        await serialize_group(session, sanction.chat_id)
        await session.refresh(job)
        await session.refresh(sanction)
        if state == "PENDING" and job.attempts >= MAX_ATTEMPTS:
            state = "FAILED"
        changed = await session.execute(
            update(GroupSanctionJob)
            .execution_options(synchronize_session=False)
            .where(
                GroupSanctionJob.id == job_id,
                GroupSanctionJob.status == "PROCESSING",
                GroupSanctionJob.lease_token == token,
                GroupSanctionJob.lease_until > now(),
            )
            .values(
                status=state,
                result=result,
                available_at=now() + timedelta(seconds=max(1, delay)),
                lease_token=None,
                lease_until=None,
                updated_at=now(),
            )
        )
        if not changed.rowcount:
            await session.rollback()
            return
        if state == "SUCCEEDED":
            sanction.status = (
                "CANCELLED"
                if sanction.kind == "KICK" and result == "SCAM_PROTECTED"
                else "ACTIVE"
                if job.operation == "APPLY"
                else "COMPLETED"
                if sanction.kind == "KICK"
                else "EXPIRED"
                if job.automatic
                else "REVOKED"
            )
            if owned is not None:
                sanction.owned = owned
        elif state != "PENDING":
            sanction.status = (
                "UNCERTAIN"
                if state == "UNCERTAIN"
                else "ACTIVE"
                if job.operation == "RELEASE"
                else state
            )
        sanction.updated_at = now()
        if (
            job.operation == "APPLY"
            and state == "SUCCEEDED"
            and result == "APPLIED"
            and sanction.replaces_sanction_id is not None
        ):
            predecessor = await session.get(GroupSanction, sanction.replaces_sanction_id)
            assert predecessor is not None
            # Only a confirmed native write retires the prior restriction. On
            # rejection it stays active; on timeout both records remain for review.
            predecessor.status, predecessor.updated_at = "COMPLETED", now()
            await session.execute(
                update(GroupSanctionJob)
                .where(
                    GroupSanctionJob.sanction_id == predecessor.id,
                    GroupSanctionJob.operation == "RELEASE",
                    GroupSanctionJob.status == "PENDING",
                    GroupSanctionJob.lease_token.is_(None),
                    GroupSanctionJob.lease_until.is_(None),
                )
                .values(status="CANCELLED", result="REPLACED", updated_at=now())
            )
            from app.models import GroupModerationDraft

            await session.execute(
                update(GroupModerationDraft)
                .where(
                    GroupModerationDraft.sanction_id == predecessor.id,
                )
                .values(receipt_done=False, receipt_checked_at=None)
            )
            audit(
                session,
                "sanction_replaced",
                job.actor_id or 0,
                sanction.chat_id,
                user_id=sanction.telegram_id,
                sanction_id=predecessor.id,
                replacement_id=sanction.id,
                replacement_kind=sanction.kind,
            )
        if job.operation == "APPLY" and state in {"FAILED", "CANCELLED"}:
            await cancel_unused_releases(session, sanction, job.actor_id or 0)
        if job.operation == "RELEASE" and state == "SUCCEEDED":
            from app.models import GroupModerationDraft

            await session.execute(
                update(GroupModerationDraft)
                .where(
                    GroupModerationDraft.sanction_id == sanction.id,
                    GroupModerationDraft.kind == sanction.kind,
                    GroupModerationDraft.panel_message_id.is_not(None),
                )
                .values(receipt_done=False, receipt_checked_at=None)
            )
        from app.warning_services import complete_warning_mute

        await complete_warning_mute(session, sanction, job, state, result)
        audit(
            session,
            "attempt",
            job.actor_id or 0,
            sanction.chat_id,
            user_id=sanction.telegram_id,
            sanction_id=sanction_id,
            job_id=job_id,
            operation=job.operation,
            attempt=job.attempts,
            state=state,
            result=result,
            success=state == "SUCCEEDED",
            reason=reason,
        )
        await session.commit()
        structlog.get_logger().info(
            "local_group_moderation_result",
            chat_id=sanction.chat_id,
            user_id=sanction.telegram_id,
            job_id=job_id,
            operation=job.operation,
            result=result,
            state=state,
            success=state == "SUCCEEDED",
            reason=reason,
        )

    async def process(self, *, limit: int = 1, sanction_id: int | None = None) -> int:
        processed = await self.recover()
        if sanction_id is not None:
            from app.models import GroupWarningEscalation

            async with self.sessions() as session:
                automatic = await session.scalar(
                    select(GroupWarningEscalation.sanction_id).where(
                        GroupWarningEscalation.trigger_id == sanction_id,
                        GroupWarningEscalation.state == "PENDING",
                    )
                )
                if automatic is not None:
                    sanction_id = automatic
        for _ in range(min(max(limit, 0), 5)):
            claim = await self.claim(sanction_id)
            if claim is None:
                break
            await self.execute(*claim)
            processed += 1
        from app.warning_services import sync_warning_events

        async with self.sessions() as session:
            await sync_warning_events(session)
        from app.bot.local_admin import publish_receipts
        from app.bot.moderation_controls import publish_warning_announcements

        await publish_warning_announcements(self.sessions, self.bot)
        await publish_receipts(self.sessions, self.bot)
        return processed

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
from sqlalchemy import or_, select, update

from app.bot.moderation_lock import user_moderation_lock
from app.errors import DomainError
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


def deadline(value: Any) -> int | None:
    return int(utc(value).timestamp()) if value is not None and value.timestamp() > 0 else None


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

    async def claim(self) -> tuple[int, str] | None:
        async with self.sessions() as session:
            candidates = list(
                await session.scalars(
                    select(GroupSanctionJob.id)
                    .join(GroupSanction, GroupSanction.id == GroupSanctionJob.sanction_id)
                    .where(
                        GroupSanctionJob.status == "PENDING",
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
                        audit(
                            session,
                            "cancelled",
                            job.actor_id or 0,
                            sanction.chat_id,
                            sanction_id=sanction.id,
                            job_id=job.id,
                            result=job.result,
                        )
                        await session.commit()
                        continue
                elif sanction.status not in {"ACTIVE", "REVOKING"}:
                    if sanction.status == "PENDING":
                        # Apply may be waiting on a Telegram flood limit; never release it early.
                        await session.commit()
                        continue
                    job.status, job.result = "CANCELLED", "NO_CONFIRMED_RESTRICTION"
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
                audit(
                    session,
                    "cancelled",
                    job.actor_id or 0,
                    sanction.chat_id,
                    sanction_id=sanction.id,
                    job_id=job.id,
                    result=result,
                )
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
            ):
                return
            sanction = await session.get(GroupSanction, job.sanction_id)
            assert sanction is not None
            try:
                await lock_group(session, sanction.chat_id)
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
                await lock_group(session, sanction.chat_id)
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
                if job.operation == "RELEASE":
                    user = await Repository(session).user_by_telegram(sanction.telegram_id)
                    scam = user is not None and await Repository(session).has_scam_identity(user)
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
                        if sanction.kind != "BAN":
                            raise DomainError("group_moderation_existing_restriction")
                        result, owned = "ALREADY_BANNED", False
                    else:
                        if member.status == "restricted":
                            raise DomainError("group_moderation_existing_restriction")
                        sent = True
                        if sanction.kind == "BAN":
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
                    expected = "kicked" if sanction.kind == "BAN" else "restricted"
                    if member.status != expected:
                        result = "ALREADY_CLEAR"
                    elif deadline(getattr(member, "until_date", None)) != deadline(until):
                        result = "RESTRICTION_CHANGED"
                    elif sanction.kind == "MUTE" and any(
                        getattr(member, key, None) is not False
                        for key in MUTE_PERMISSIONS.model_dump(exclude_none=True)
                    ):
                        result = "RESTRICTION_CHANGED"
                    elif sanction.kind == "BAN":
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
                        chat = await asyncio.wait_for(
                            self.bot.get_chat(sanction.chat_id), API_TIMEOUT
                        )
                        permissions = getattr(chat, "permissions", None)
                        if not isinstance(permissions, ChatPermissions):
                            raise DomainError("group_moderation_permissions_unavailable")
                        sent = True
                        success = await asyncio.wait_for(
                            self.bot.restrict_chat_member(
                                chat_id=sanction.chat_id,
                                user_id=sanction.telegram_id,
                                permissions=permissions,
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
                )
            except DomainError as error:
                await session.rollback()
                await self.finish(session, job, sanction, token, "CANCELLED", error.code)
            except (TelegramBadRequest, TelegramForbiddenError) as error:
                await session.rollback()
                await self.finish(session, job, sanction, token, "FAILED", type(error).__name__)
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
                )

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
                "ACTIVE" if job.operation == "APPLY" else "EXPIRED" if job.automatic else "REVOKED"
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
        )

    async def process(self, *, limit: int = 1) -> int:
        processed = await self.recover()
        for _ in range(min(max(limit, 0), 5)):
            claim = await self.claim()
            if claim is None:
                break
            await self.execute(*claim)
            processed += 1
        return processed

"""Managed groups, bot-observed member backup and consent-based recovery outboxes."""

import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import BigInteger, cast, func, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.models import (
    AuditEvent,
    BanAction,
    ManagedGroup,
    ObservedMember,
    PrivateContact,
    RecoveryCampaign,
    RecoveryDelivery,
    RecoverySubscription,
    ScamRecord,
    User,
    now,
)
from app.services import DomainError, Service


@dataclass(frozen=True)
class BanSummary:
    """Live protected-group results; failed attempts remain part of pending."""

    telegram_id: int | None
    checked: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    already_banned: int = 0


async def enqueue_scam_bans(session: AsyncSession, record: ScamRecord) -> int:
    if record.status != "ACTIVE":
        return 0
    user = await session.get(User, record.target_id)
    if user is None or user.telegram_id is None:
        return 0
    groups = list(
        (
            await session.scalars(
                select(ManagedGroup).where(
                    ManagedGroup.approved.is_(True),
                    ManagedGroup.enabled.is_(True),
                )
            )
        ).all()
    )
    for group in groups:
        await enqueue_ban(session, group.chat_id, user.telegram_id, record.id)
    return len(groups)


async def enqueue_ban(
    session: AsyncSession, chat_id: int, telegram_id: int, scam_record_id: int
) -> None:
    group = await session.get(ManagedGroup, chat_id, populate_existing=True)
    if group is None or not group.approved or not group.enabled:
        return
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
    await session.execute(
        insert(BanAction)
        .values(
            chat_id=chat_id,
            telegram_id=telegram_id,
            scam_record_id=scam_record_id,
            status="PENDING",
            attempts=0,
            next_attempt_at=now(),
        )
        .on_conflict_do_update(
            index_elements=["chat_id", "telegram_id", "scam_record_id"],
            set_={
                "status": "PENDING",
                "attempts": 0,
                "next_attempt_at": now(),
                "completed_at": None,
            },
            where=BanAction.status == "OBSOLETE",
        )
    )


class GroupService:
    def __init__(self, settings: Settings, session: AsyncSession):
        self.settings, self.session = settings, session
        self.core = Service(settings, session)

    def require_owner(self, actor: int) -> None:
        if self.settings.group_owner is None or actor != self.settings.group_owner:
            raise DomainError("forbidden")

    async def stage_group(
        self, actor: int, chat_id: int, title: str, chat_type: str = "supergroup"
    ) -> ManagedGroup:
        self.require_owner(actor)
        if chat_id >= 0 or chat_type not in ("group", "supergroup"):
            raise DomainError("invalid_input")
        await self.core.repo.lock_identity_metadata()
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None:
            group = ManagedGroup(
                chat_id=chat_id,
                title=title[:256],
                chat_type=chat_type,
                approved=False,
                enabled=False,
                can_restrict_members=False,
            )
            self.session.add(group)
            self.core._audit(actor, "group_staged", None, chat_id=chat_id)
        await self.session.commit()
        return group

    async def register_group(
        self,
        actor: int,
        chat_id: int,
        title: str,
        can_restrict_members: bool,
        chat_type: str = "supergroup",
    ) -> ManagedGroup:
        self.require_owner(actor)
        if chat_id >= 0 or chat_type not in ("group", "supergroup"):
            raise DomainError("invalid_input")
        await self.core.repo.lock_identity_metadata()
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None:
            group = ManagedGroup(
                chat_id=chat_id,
                chat_type=chat_type,
                title=title[:256],
                approved=True,
                enabled=True,
                can_restrict_members=can_restrict_members,
            )
            self.session.add(group)
        else:
            group.title, group.enabled, group.can_restrict_members = (
                title[:256],
                True,
                can_restrict_members,
            )
        group.approved = True
        group.chat_type = chat_type
        await self.session.flush()
        rows = (
            await self.session.execute(
                select(ScamRecord.id, User.telegram_id)
                .join(User, User.id == ScamRecord.target_id)
                .where(ScamRecord.status == "ACTIVE", User.telegram_id.is_not(None))
            )
        ).all()
        for record_id, telegram_id in rows:
            await enqueue_ban(self.session, chat_id, telegram_id, record_id)
        if can_restrict_members:
            await self.session.execute(
                update(BanAction)
                .where(
                    BanAction.chat_id == chat_id,
                    BanAction.status == "FAILED",
                    (BanAction.result_type != "TelegramRetryAfter")
                    | (BanAction.next_attempt_at <= now()),
                )
                .values(status="PENDING", attempts=0, next_attempt_at=now())
            )
        self.core._audit(
            actor,
            "group_registered",
            None,
            chat_id=chat_id,
            enabled=True,
            can_restrict_members=can_restrict_members,
        )
        await self.session.commit()
        return group

    async def note_group_permissions(self, chat_id: int, can_restrict_members: bool) -> None:
        """Rights loss is a retryable delivery problem, not owner withdrawal of protection."""
        await self.core.repo.lock_identity_metadata()
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is not None:
            group.can_restrict_members = can_restrict_members
            if group.approved and group.enabled and can_restrict_members:
                await self.session.execute(
                    update(BanAction)
                    .where(
                        BanAction.chat_id == chat_id,
                        BanAction.status == "FAILED",
                        (BanAction.result_type != "TelegramRetryAfter")
                        | (BanAction.next_attempt_at <= now()),
                    )
                    .values(status="PENDING", attempts=0, next_attempt_at=now(), completed_at=None)
                    .execution_options(synchronize_session=False)
                )
        await self.session.commit()

    async def disable_group(self, chat_id: int) -> None:
        await self.session.execute(
            update(ManagedGroup)
            .where(ManagedGroup.chat_id == chat_id)
            .values(enabled=False, can_restrict_members=False)
        )
        await self.session.commit()

    async def observe_member(
        self, chat_id: int, tg_id: int, username: str | None, name: str
    ) -> None:
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if tg_id <= 0 or group is None or not group.approved:
            return
        await self.core.observe(tg_id, username, name)
        await self.core.repo.lock_identity_metadata()
        active = await self.session.scalar(
            select(ScamRecord.id)
            .join(User, User.id == ScamRecord.target_id)
            .where(
                ScamRecord.status == "ACTIVE",
                (User.telegram_id == tg_id)
                | (
                    User.telegram_id.is_(None)
                    & User.username.is_not(None)
                    & (User.username == (username.lower().lstrip("@") if username else None))
                ),
            )
            .limit(1)
        )
        if active is not None:
            await self.session.commit()
            await self.check_member(chat_id, tg_id)
            return
        if self.session.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
        await self.session.execute(
            insert(ObservedMember)
            .values(
                chat_id=chat_id,
                telegram_id=tg_id,
                username=username,
                display_name=name[:256],
                observed_at=now(),
            )
            .on_conflict_do_update(
                index_elements=["chat_id", "telegram_id"],
                set_={"username": username, "display_name": name[:256], "observed_at": now()},
            )
        )
        await self.session.commit()
        await self.check_member(chat_id, tg_id)

    async def check_member(self, chat_id: int, tg_id: int, fresh_join: bool = False) -> None:
        await self.core.repo.lock_identity_metadata()
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None or not group.approved or not group.enabled:
            await self.session.commit()
            return
        record = await self.session.scalar(
            select(ScamRecord)
            .join(User, User.id == ScamRecord.target_id)
            .where(User.telegram_id == tg_id, ScamRecord.status == "ACTIVE")
        )
        if record:
            await enqueue_ban(self.session, chat_id, tg_id, record.id)
            presence = await self.session.scalar(
                select(AuditEvent.id)
                .where(
                    AuditEvent.action == "scam_member_present",
                    AuditEvent.target_id == record.target_id,
                    cast(AuditEvent.details["chat_id"].as_string(), BigInteger) == chat_id,
                    AuditEvent.details["record_id"].as_integer() == record.id,
                )
                .limit(1)
            )
            if presence is None:
                self.core._audit(
                    tg_id,
                    "scam_member_present",
                    record.target_id,
                    chat_id=chat_id,
                    record_id=record.id,
                )
            # Keep successful-ban repeat checks bounded. A first observed presence
            # after a failed preemptive ban gets an immediate attempt; its audit
            # marker prevents subsequent messages from repeatedly bypassing backoff.
            await self.session.execute(
                update(BanAction)
                .where(
                    BanAction.chat_id == chat_id,
                    BanAction.telegram_id == tg_id,
                    BanAction.scam_record_id == record.id,
                    BanAction.status == "SUCCEEDED",
                    (
                        true()
                        if fresh_join
                        else BanAction.completed_at <= now() - timedelta(seconds=60)
                    ),
                )
                .values(status="PENDING", attempts=0, next_attempt_at=now(), completed_at=None)
                .execution_options(synchronize_session=False)
            )
            failed = await self.session.scalar(
                select(BanAction)
                .where(
                    BanAction.chat_id == chat_id,
                    BanAction.telegram_id == tg_id,
                    BanAction.scam_record_id == record.id,
                    BanAction.status == "FAILED",
                    (BanAction.result_type != "TelegramRetryAfter")
                    | (BanAction.next_attempt_at <= now()),
                )
                .execution_options(populate_existing=True)
            )
            if failed is not None:
                recent = await self.session.scalar(
                    select(AuditEvent.id)
                    .where(
                        AuditEvent.action == "scam_presence_retry",
                        AuditEvent.actor_id == tg_id,
                        AuditEvent.target_id == record.target_id,
                        cast(AuditEvent.details["chat_id"].as_string(), BigInteger) == chat_id,
                        AuditEvent.details["record_id"].as_integer() == record.id,
                        AuditEvent.created_at >= now() - timedelta(seconds=60),
                    )
                    .limit(1)
                )
                if fresh_join or recent is None:
                    failed.status, failed.attempts = "PENDING", 0
                    failed.next_attempt_at, failed.completed_at = now(), None
                    self.core._audit(
                        tg_id,
                        "scam_presence_retry",
                        record.target_id,
                        chat_id=chat_id,
                        record_id=record.id,
                    )
        await self.session.commit()

    async def mark_private_contact(self, actor: int) -> None:
        if actor <= 0:
            raise DomainError("invalid_input")
        if self.session.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert  # type: ignore[assignment]
        await self.session.execute(
            insert(PrivateContact)
            .values(telegram_id=actor, started_at=now())
            .on_conflict_do_nothing()
        )
        await self.session.commit()

    async def subscribe(self, actor: int, chat_id: int, consent: bool) -> bool:
        if type(consent) is not bool:
            raise DomainError("invalid_input")
        await self.core.repo.lock_identity_metadata()
        if (
            await self.session.get(PrivateContact, actor) is None
            or await self.session.get(ManagedGroup, chat_id) is None
        ):
            await self.session.rollback()
            raise DomainError("not_found")
        subscription = await self.session.get(
            RecoverySubscription, (chat_id, actor), populate_existing=True
        )
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if consent and (
            group is None
            or not group.approved
            or await self.session.get(ObservedMember, (chat_id, actor)) is None
        ):
            await self.session.rollback()
            raise DomainError("forbidden")
        if subscription is None:
            self.session.add(
                RecoverySubscription(chat_id=chat_id, telegram_id=actor, consent=consent)
            )
        else:
            subscription.consent = consent
        await self.session.commit()
        return consent

    async def groups(self, actor: int) -> list[ManagedGroup]:
        self.require_owner(actor)
        return list(
            (await self.session.scalars(select(ManagedGroup).order_by(ManagedGroup.chat_id))).all()
        )

    async def available_groups(self, actor: int) -> list[ManagedGroup]:
        return await self.groups_for_subscription(actor)

    async def export_members(self, actor: int, chat_id: int) -> list[dict]:
        self.require_owner(actor)
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None or not group.approved:
            raise DomainError("not_found")
        excluded = (
            select(ScamRecord.id)
            .join(User, User.id == ScamRecord.target_id)
            .where(
                ScamRecord.status == "ACTIVE",
                (User.telegram_id == ObservedMember.telegram_id)
                | (
                    User.telegram_id.is_(None)
                    & User.username.is_not(None)
                    & (User.username == func.lower(ObservedMember.username))
                ),
            )
            .exists()
        )
        rows = list(
            (
                await self.session.scalars(
                    select(ObservedMember)
                    .where(ObservedMember.chat_id == chat_id, ~excluded)
                    .order_by(ObservedMember.telegram_id)
                )
            ).all()
        )
        self.core._audit(actor, "group_members_exported", None, chat_id=chat_id, count=len(rows))
        await self.session.commit()
        return [
            {
                "telegram_id": r.telegram_id,
                "username": r.username,
                "display_name": r.display_name,
                "observed_at": r.observed_at.isoformat(),
            }
            for r in rows
        ]

    @staticmethod
    def _invite(url: str) -> str:
        parsed = urlsplit(url)
        if (
            len(url) > 512
            or parsed.scheme != "https"
            or parsed.netloc != "t.me"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(
                r"/(?:\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+|[A-Za-z][A-Za-z0-9_]{4,31})",
                parsed.path,
            )
        ):
            raise DomainError("invalid_input")
        return url

    async def prepare_recovery(
        self, actor: int, chat_id: int, invite_url: str, request_key: str
    ) -> dict:
        self.require_owner(actor)
        self.core._request_key(request_key)
        self._invite(invite_url)
        if await self.session.get(ManagedGroup, chat_id) is None:
            raise DomainError("not_found")
        recipients = list(
            (
                await self.session.scalars(
                    select(RecoverySubscription.telegram_id).where(
                        RecoverySubscription.chat_id == chat_id,
                        RecoverySubscription.consent.is_(True),
                    )
                )
            ).all()
        )
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None or not group.approved:
            raise DomainError("not_found")
        return {"recipients": len(recipients), "chat_id": chat_id, "title": group.title}

    async def queue_recovery(
        self, actor: int, chat_id: int, invite_url: str, request_key: str
    ) -> dict:
        await self.prepare_recovery(actor, chat_id, invite_url, request_key)
        await self.core.repo.lock_identity_metadata()
        group = await self.session.get(ManagedGroup, chat_id, populate_existing=True)
        if group is None or not group.approved:
            await self.session.rollback()
            raise DomainError("not_found")
        previous = await self.session.scalar(
            select(RecoveryCampaign).where(RecoveryCampaign.request_key == request_key)
        )
        if previous:
            if (
                previous.actor_id != actor
                or previous.chat_id != chat_id
                or previous.invite_url != invite_url
            ):
                await self.session.rollback()
                raise DomainError("invalid_input")
            await self.session.commit()
            return {"campaign_id": previous.id, "queued": 0}
        campaign = RecoveryCampaign(
            chat_id=chat_id, actor_id=actor, invite_url=invite_url, request_key=request_key
        )
        self.session.add(campaign)
        await self.session.flush()
        recipients = list(
            (
                await self.session.scalars(
                    select(RecoverySubscription.telegram_id).where(
                        RecoverySubscription.chat_id == chat_id,
                        RecoverySubscription.consent.is_(True),
                    )
                )
            ).all()
        )
        for recipient in recipients:
            self.session.add(RecoveryDelivery(campaign_id=campaign.id, telegram_id=recipient))
        self.core._audit(
            actor,
            "recovery_queued",
            None,
            chat_id=chat_id,
            recipients=len(recipients),
            campaign_id=campaign.id,
        )
        await self.session.commit()
        return {"campaign_id": campaign.id, "queued": len(recipients)}

    async def ban_summary(self, record_id: int) -> BanSummary:
        record = await self.session.get(ScamRecord, record_id, populate_existing=True)
        user = (
            await self.session.get(User, record.target_id, populate_existing=True)
            if record is not None
            else None
        )
        if user is None or user.telegram_id is None:
            return BanSummary(telegram_id=None)
        if record is None or record.status != "ACTIVE":
            return BanSummary(telegram_id=user.telegram_id)
        results = list(
            (
                await self.session.execute(
                    select(ManagedGroup.chat_id, BanAction.status, BanAction.result_type)
                    .outerjoin(
                        BanAction,
                        (BanAction.chat_id == ManagedGroup.chat_id)
                        & (BanAction.telegram_id == user.telegram_id)
                        & (BanAction.scam_record_id == record_id),
                    )
                    .where(ManagedGroup.approved.is_(True), ManagedGroup.enabled.is_(True))
                )
            ).all()
        )
        succeeded = sum(status == "SUCCEEDED" for _, status, _ in results)
        return BanSummary(
            telegram_id=user.telegram_id,
            checked=len(results),
            succeeded=succeeded,
            failed=sum(status == "FAILED" for _, status, _ in results),
            pending=len(results) - succeeded,
            already_banned=sum(
                status == "SUCCEEDED" and result == "ALREADY_BANNED"
                for _, status, result in results
            ),
        )

    async def claim_bans(
        self, limit: int = 20, *, scam_record_id: int | None = None
    ) -> list[BanAction]:
        return await self._claim(BanAction, limit, scam_record_id=scam_record_id)

    async def has_ready_bans(self, record_id: int) -> bool:
        """A rejected obsolete claim must not hide later due jobs for this record."""
        ready = await self.session.scalar(
            select(BanAction.id)
            .where(
                BanAction.scam_record_id == record_id,
                BanAction.status.in_(["PENDING", "FAILED"]),
                BanAction.next_attempt_at <= now(),
                BanAction.attempts < 8,
            )
            .limit(1)
        )
        await self.session.commit()
        return ready is not None

    async def claim_deliveries(self, limit: int = 20) -> list[RecoveryDelivery]:
        return await self._claim(RecoveryDelivery, limit)

    async def _claim(self, model, limit, *, scam_record_id=None):
        await self.core.repo.lock_identity_metadata()
        stale = now() - timedelta(minutes=5)
        await self.session.execute(
            update(model)
            .where(model.status == "PROCESSING", model.claimed_at < stale)
            .values(status="FAILED", next_attempt_at=now())
            .execution_options(synchronize_session=False)
        )
        query = (
            select(model)
            .execution_options(populate_existing=True)
            .where(
                model.status.in_(["PENDING", "FAILED"]),
                model.next_attempt_at <= now(),
                model.attempts < 8,
            )
            .order_by(model.id)
            .limit(min(max(limit, 1), 100))
        )
        if model is RecoveryDelivery:
            query = query.options(selectinload(RecoveryDelivery.campaign))
        elif scam_record_id is not None:
            query = query.where(BanAction.scam_record_id == scam_record_id)
        rows = list((await self.session.scalars(query)).all())
        valid = []
        for row in rows:
            if model is BanAction:
                record = await self.session.get(
                    ScamRecord, row.scam_record_id, populate_existing=True
                )
                group = await self.session.get(ManagedGroup, row.chat_id, populate_existing=True)
                target = (
                    await self.session.get(User, record.target_id, populate_existing=True)
                    if record is not None
                    else None
                )
                eligible = (
                    record is not None
                    and record.status == "ACTIVE"
                    and group is not None
                    and group.approved
                    and group.enabled
                    and target is not None
                    and target.telegram_id == row.telegram_id
                )
            else:
                subscription = await self.session.get(
                    RecoverySubscription,
                    (row.campaign.chat_id, row.telegram_id),
                    populate_existing=True,
                )
                group = await self.session.get(
                    ManagedGroup, row.campaign.chat_id, populate_existing=True
                )
                eligible = (
                    group is not None
                    and group.approved
                    and subscription is not None
                    and subscription.consent
                )
            if not eligible:
                row.status, row.completed_at = "OBSOLETE", now()
                continue
            row.status, row.claimed_at = "PROCESSING", now()
            row.attempts += 1
            valid.append(row)
        await self.session.commit()
        return valid

    async def finish_ban(
        self,
        action_id: int,
        success: bool,
        result_type: str,
        retry_after: int | None = None,
        permanent: bool = False,
    ) -> None:
        await self._finish(BanAction, action_id, success, result_type, retry_after, permanent)

    async def finish_delivery(
        self,
        action_id: int,
        success: bool,
        result_type: str,
        retry_after: int | None = None,
        permanent: bool = False,
    ) -> None:
        await self._finish(
            RecoveryDelivery, action_id, success, result_type, retry_after, permanent
        )

    async def _finish(
        self, model, action_id, success, result_type, retry_after=None, permanent=False
    ):
        row = await self.session.get(model, action_id)
        if row is None or row.status != "PROCESSING":
            return
        row.status = "SUCCEEDED" if success else "FAILED"
        if permanent and not success:
            row.attempts = 8
        row.result_type = re.sub(r"[^A-Za-z0-9_]", "", result_type)[:64]
        row.completed_at = now()
        row.next_attempt_at = now() + timedelta(
            seconds=max(1, retry_after)
            if retry_after is not None
            else min(3600, 2**row.attempts * 5)
        )
        await self.session.commit()

    async def groups_for_subscription(self, actor: int) -> list[ManagedGroup]:
        if await self.session.get(PrivateContact, actor) is None:
            raise DomainError("not_found")
        return list(
            (
                await self.session.scalars(
                    select(ManagedGroup)
                    .join(ObservedMember, ObservedMember.chat_id == ManagedGroup.chat_id)
                    .where(ObservedMember.telegram_id == actor, ManagedGroup.approved.is_(True))
                    .order_by(ManagedGroup.chat_id)
                )
            ).all()
        )

    async def ban_eligible(self, action_id: int) -> bool:
        row = await self.session.get(BanAction, action_id, populate_existing=True)
        if row is None or row.status != "PROCESSING":
            return False
        record = await self.session.get(ScamRecord, row.scam_record_id, populate_existing=True)
        group = await self.session.get(ManagedGroup, row.chat_id, populate_existing=True)
        target = (
            await self.session.get(User, record.target_id, populate_existing=True)
            if record is not None
            else None
        )
        eligible = (
            record is not None
            and record.status == "ACTIVE"
            and group is not None
            and group.approved
            and group.enabled
            and target is not None
            and target.telegram_id == row.telegram_id
        )
        if not eligible:
            row.status, row.completed_at = "OBSOLETE", now()
        await self.session.commit()
        return eligible

    async def delivery_eligible(self, action_id: int) -> bool:
        row = await self.session.get(RecoveryDelivery, action_id, populate_existing=True)
        if row is None or row.status != "PROCESSING":
            return False
        campaign = await self.session.get(RecoveryCampaign, row.campaign_id)
        subscription = (
            await self.session.get(
                RecoverySubscription, (campaign.chat_id, row.telegram_id), populate_existing=True
            )
            if campaign
            else None
        )
        group = (
            await self.session.get(ManagedGroup, campaign.chat_id, populate_existing=True)
            if campaign
            else None
        )
        eligible = (
            group is not None
            and group.approved
            and subscription is not None
            and subscription.consent
        )
        if not eligible:
            row.status, row.completed_at = "OBSOLETE", now()
        await self.session.commit()
        return eligible

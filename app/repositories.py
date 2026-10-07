"""Database queries and locking; transaction boundaries belong to Service."""

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from app.models import (
    Administrator,
    AdministratorChange,
    AuditEvent,
    BanAction,
    IdentityLock,
    OperationLock,
    RecoveryDelivery,
    Report,
    ReputationAdjustment,
    ReputationEvent,
    ReputationRequest,
    ScamRecord,
    TopVisibility,
    TopVisibilityAction,
    TrustedAction,
    TrustedDesignation,
    User,
    UsernameHistory,
    now,
)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_identity_metadata(self) -> None:
        # Serializes identity metadata and owner administrator decisions until commit.
        # Never hold across Telegram network calls.
        if self.session.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            await self.session.execute(
                pg_insert(IdentityLock).values(id=1).on_conflict_do_nothing()
            )
        else:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            await self.session.execute(
                sqlite_insert(IdentityLock).values(id=1).on_conflict_do_nothing()
            )
        await self.session.execute(update(IdentityLock).where(IdentityLock.id == 1).values(id=1))

    async def user_by_telegram(self, telegram_id: int) -> User | None:
        return await self.session.scalar(
            select(User)
            .where(User.telegram_id == telegram_id)
            .execution_options(populate_existing=True)
        )

    async def language(self, telegram_id: int) -> str | None:
        return await self.session.scalar(
            select(User.language).where(User.telegram_id == telegram_id)
        )

    async def set_language(self, telegram_id: int, language: str) -> None:
        await self.session.execute(
            update(User).where(User.telegram_id == telegram_id).values(language=language)
        )

    async def user_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id, populate_existing=True)

    async def user_by_username(self, username: str) -> User | None:
        return await self.session.scalar(
            select(User)
            .execution_options(populate_existing=True)
            .where(User.username == username)
            .order_by(User.telegram_id.desc().nulls_last())
            .limit(1)
        )

    async def latest_username(self, user_id: int) -> UsernameHistory | None:
        return await self.session.scalar(
            select(UsernameHistory)
            .where(UsernameHistory.user_id == user_id)
            .order_by(UsernameHistory.id.desc())
            .limit(1)
        )

    async def retire_other_username_owners(self, username: str, user_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.username == username, User.telegram_id.is_not(None), User.id != user_id)
            .values(username=None)
        )

    async def lock_user(self, user_id: int) -> OperationLock | None:
        # A no-op UPDATE serializes writes on PostgreSQL and SQLite alike.
        await self.session.execute(
            update(OperationLock).where(OperationLock.user_id == user_id).values(user_id=user_id)
        )
        return await self.session.get(OperationLock, user_id, populate_existing=True)

    async def active_scam(self, target_id: int) -> ScamRecord | None:
        return await self.session.scalar(
            select(ScamRecord).where(
                ScamRecord.target_id == target_id, ScamRecord.status == "ACTIVE"
            )
        )

    async def rep_stats(self, user_id: int) -> tuple[int, int, int]:
        # A single SQL statement provides a coherent READ COMMITTED snapshot,
        # even when moderation or a compensating reset commits concurrently.
        legacy = select(
            case((ReputationEvent.value == 1, 1), else_=0).label("positive"),
            case((ReputationEvent.value == -1, 1), else_=0).label("negative"),
        ).where(ReputationEvent.receiver_user_id == user_id)
        approved = select(
            case((ReputationRequest.value == 1, 1), else_=0).label("positive"),
            case((ReputationRequest.value == -1, 1), else_=0).label("negative"),
        ).where(
            ReputationRequest.receiver_user_id == user_id, ReputationRequest.status == "APPROVED"
        )
        adjustments = select(
            ReputationAdjustment.positive_delta.label("positive"),
            ReputationAdjustment.negative_delta.label("negative"),
        ).where(ReputationAdjustment.target_id == user_id)
        ledger = legacy.union_all(approved, adjustments).subquery()
        positive, negative = (
            await self.session.execute(
                select(
                    func.coalesce(func.sum(ledger.c.positive), 0),
                    func.coalesce(func.sum(ledger.c.negative), 0),
                )
            )
        ).one()
        return int(positive) - int(negative), int(positive), int(negative)

    async def vote_by_request(self, key: str) -> ReputationRequest | ReputationEvent | None:
        return await self.session.scalar(
            select(ReputationRequest).where(ReputationRequest.request_key == key)
        ) or await self.session.scalar(
            select(ReputationEvent).where(ReputationEvent.request_key == key)
        )

    async def vote_by_pair(
        self, giver_id: int, receiver_id: int
    ) -> ReputationRequest | ReputationEvent | None:
        return await self.session.scalar(
            select(ReputationRequest).where(
                ReputationRequest.giver_user_id == giver_id,
                ReputationRequest.receiver_user_id == receiver_id,
            )
        ) or await self.session.scalar(
            select(ReputationEvent).where(
                ReputationEvent.giver_user_id == giver_id,
                ReputationEvent.receiver_user_id == receiver_id,
            )
        )

    async def reputation_request(self, reference: str) -> ReputationRequest | None:
        return await self.session.scalar(
            select(ReputationRequest)
            .where(ReputationRequest.reference == reference)
            .options(
                selectinload(ReputationRequest.giver), selectinload(ReputationRequest.receiver)
            )
            .execution_options(populate_existing=True)
        )

    async def pending_reputation(
        self, offset: int, limit: int
    ) -> tuple[list[ReputationRequest], int]:
        condition = ReputationRequest.status == "PENDING"
        rows = list(
            (
                await self.session.scalars(
                    select(ReputationRequest)
                    .where(condition)
                    .options(
                        selectinload(ReputationRequest.giver),
                        selectinload(ReputationRequest.receiver),
                    )
                    .order_by(ReputationRequest.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return rows, int(
            await self.session.scalar(
                select(func.count()).select_from(ReputationRequest).where(condition)
            )
            or 0
        )

    async def adjustment_by_key(self, key: str) -> ReputationAdjustment | None:
        return await self.session.scalar(
            select(ReputationAdjustment).where(ReputationAdjustment.request_key == key)
        )

    async def top_action_by_key(self, key: str) -> TopVisibilityAction | None:
        return await self.session.scalar(
            select(TopVisibilityAction).where(TopVisibilityAction.request_key == key)
        )

    async def top_visibility(self, target_id: int) -> TopVisibility | None:
        return await self.session.get(TopVisibility, target_id, populate_existing=True)

    async def report_by_request(self, key: str) -> Report | None:
        return await self.session.scalar(select(Report).where(Report.request_key == key))

    async def pending_pair(self, reporter_id: int, target_id: int) -> Report | None:
        return await self.session.scalar(
            select(Report).where(
                Report.reporter_id == reporter_id,
                Report.target_id == target_id,
                Report.status == "PENDING",
            )
        )

    async def report_by_reference(self, reference: str, *, details: bool = False) -> Report | None:
        query = select(Report).where(Report.reference == reference)
        if details:
            query = query.options(
                selectinload(Report.target),
                selectinload(Report.reporter),
                selectinload(Report.evidence),
            )
        return await self.session.scalar(query)

    async def leaderboard_entries(
        self, *, owner_id: int | None = None, admin_ids: frozenset[int] = frozenset()
    ) -> list[dict]:
        active_admin = (
            select(Administrator.telegram_id)
            .where(Administrator.telegram_id == User.telegram_id, Administrator.active.is_(True))
            .exists()
        )
        configured_admin = (
            User.telegram_id.in_(admin_ids)
            & ~select(Administrator.telegram_id)
            .where(Administrator.telegram_id == User.telegram_id)
            .exists()
        )
        role_trusted = active_admin | configured_admin
        if owner_id is not None:
            role_trusted = role_trusted | (User.telegram_id == owner_id)
        observed = aliased(User)
        superseded = User.telegram_id.is_(None) & (
            select(observed.id)
            .where(observed.telegram_id.is_not(None), observed.username == User.username)
            .exists()
        )
        scam_target = aliased(User)
        active_scam = (
            select(ScamRecord.id)
            .join(scam_target, scam_target.id == ScamRecord.target_id)
            .where(
                ScamRecord.status == "ACTIVE",
                (ScamRecord.target_id == User.id)
                | (
                    scam_target.telegram_id.is_(None)
                    & scam_target.username.is_not(None)
                    & (scam_target.username == User.username)
                ),
            )
            .exists()
        )
        legacy = select(
            ReputationEvent.receiver_user_id.label("user_id"), ReputationEvent.value.label("score")
        )
        approved = select(
            ReputationRequest.receiver_user_id.label("user_id"),
            ReputationRequest.value.label("score"),
        ).where(ReputationRequest.status == "APPROVED")
        adjustments = select(
            ReputationAdjustment.target_id.label("user_id"),
            (ReputationAdjustment.positive_delta - ReputationAdjustment.negative_delta).label(
                "score"
            ),
        )
        ledger = legacy.union_all(approved, adjustments).subquery()
        scores = (
            select(ledger.c.user_id, func.sum(ledger.c.score).label("score"))
            .group_by(ledger.c.user_id)
            .subquery()
        )
        query = (
            select(User, func.coalesce(scores.c.score, 0).label("score"))
            .outerjoin(scores, scores.c.user_id == User.id)
            .outerjoin(TopVisibility, TopVisibility.target_id == User.id)
            .outerjoin(TrustedDesignation, TrustedDesignation.target_id == User.id)
            .where(
                func.coalesce(scores.c.score, 0) >= 0,
                (TopVisibility.visible.is_(True))
                | (
                    TopVisibility.target_id.is_(None)
                    & (TrustedDesignation.active.is_(True) | role_trusted)
                ),
                ~active_scam,
                ~superseded,
            )
            .order_by(func.coalesce(scores.c.score, 0).desc(), User.id)
            .limit(10)
        )
        return [
            {"user": user, "score": int(score)}
            for user, score in (await self.session.execute(query)).all()
        ]

    async def scam_by_id(self, record_id: int) -> ScamRecord | None:
        return await self.session.scalar(
            select(ScamRecord)
            .where(ScamRecord.id == record_id)
            .options(selectinload(ScamRecord.target))
            .execution_options(populate_existing=True)
        )

    async def scam_identity_action(self, nonce: str) -> AuditEvent | None:
        return await self.session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "scam_identity_supplemented",
                AuditEvent.details["nonce"].as_string() == nonce,
            )
        )

    async def scams(self, offset: int, limit: int) -> tuple[list[ScamRecord], int]:
        condition = ScamRecord.status == "ACTIVE"
        rows = list(
            (
                await self.session.scalars(
                    select(ScamRecord)
                    .where(condition)
                    .options(selectinload(ScamRecord.target))
                    .order_by(ScamRecord.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        total = await self.session.scalar(
            select(func.count()).select_from(ScamRecord).where(condition)
        )
        return rows, total or 0

    async def pending(self, offset: int, limit: int) -> tuple[list[Report], int]:
        condition = Report.status == "PENDING"
        rows = list(
            (
                await self.session.scalars(
                    select(Report)
                    .where(condition)
                    .options(selectinload(Report.target), selectinload(Report.reporter))
                    .order_by(Report.id)
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        total = await self.session.scalar(select(func.count()).select_from(Report).where(condition))
        return rows, total or 0

    async def admin_stats(self) -> dict[str, int]:
        stats = {
            name: int(await self.session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in [
                ("users", User),
                ("reports", Report),
                ("reputation_events", ReputationEvent),
                ("scam_records", ScamRecord),
            ]
        }
        stats["pending"] = int(
            await self.session.scalar(
                select(func.count()).select_from(Report).where(Report.status == "PENDING")
            )
            or 0
        )
        stats["scams"] = int(
            await self.session.scalar(
                select(func.count()).select_from(ScamRecord).where(ScamRecord.status == "ACTIVE")
            )
            or 0
        )
        return stats

    async def audits(self, offset: int, limit: int) -> list[AuditEvent]:
        return list(
            (
                await self.session.scalars(
                    select(AuditEvent).order_by(AuditEvent.id.desc()).offset(offset).limit(limit)
                )
            ).all()
        )

    async def users(self, offset: int, limit: int) -> tuple[list[User], int]:
        target = aliased(User)
        observed = aliased(User)
        # A current observed identity replaces the username-only directory entry.
        # Keep the original record and its judgments: usernames can be reassigned.
        superseded = User.telegram_id.is_(None) & (
            select(observed.id)
            .where(
                observed.telegram_id.is_not(None),
                observed.username == User.username,
            )
            .exists()
        )
        eligible = (
            ~select(ScamRecord.id)
            .join(target, target.id == ScamRecord.target_id)
            .where(
                ScamRecord.status == "ACTIVE",
                (ScamRecord.target_id == User.id)
                | (
                    target.telegram_id.is_(None)
                    & target.username.is_not(None)
                    & (target.username == User.username)
                ),
            )
            .exists()
        ) & ~superseded
        rows = list(
            (
                await self.session.scalars(
                    select(User)
                    .where(eligible)
                    .order_by(User.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        total = await self.session.scalar(select(func.count()).select_from(User).where(eligible))
        return rows, total or 0

    async def pending_next(self, reference: str | None) -> Report | None:
        current = (
            await self.session.scalar(select(Report.id).where(Report.reference == reference))
            if reference
            else None
        )
        query = (
            select(Report)
            .where(Report.status == "PENDING")
            .options(selectinload(Report.target), selectinload(Report.reporter))
            .order_by(Report.id)
            .limit(1)
        )
        report = await self.session.scalar(query.where(Report.id > current)) if current else None
        return report if report is not None else await self.session.scalar(query)

    async def audit_count(self) -> int:
        return int(await self.session.scalar(select(func.count()).select_from(AuditEvent)) or 0)

    async def global_reset_by_key(self, request_key: str) -> AuditEvent | None:
        return await self.session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "reputation_reset_all",
                AuditEvent.details["request_key"].as_string() == request_key,
            )
        )

    async def all_user_ids(self) -> list[int]:
        return list((await self.session.scalars(select(User.id).order_by(User.id))).all())

    async def all_pending_reputation(self) -> list[ReputationRequest]:
        return list(
            (
                await self.session.scalars(
                    select(ReputationRequest)
                    .where(ReputationRequest.status == "PENDING")
                    .order_by(ReputationRequest.id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )

    async def administrator(self, telegram_id: int) -> Administrator | None:
        return await self.session.get(Administrator, telegram_id, populate_existing=True)

    async def administrator_rows(self) -> list[Administrator]:
        return list((await self.session.scalars(select(Administrator))).all())

    async def administrator_change(self, request_key: str) -> AdministratorChange | None:
        return await self.session.scalar(
            select(AdministratorChange).where(AdministratorChange.request_key == request_key)
        )

    async def trusted_designation(self, target_id: int) -> TrustedDesignation | None:
        return await self.session.get(TrustedDesignation, target_id, populate_existing=True)

    async def trusted_action(self, request_key: str) -> TrustedAction | None:
        return await self.session.scalar(
            select(TrustedAction).where(TrustedAction.request_key == request_key)
        )

    async def unresolved_scam(self, username: str | None) -> ScamRecord | None:
        if username is None:
            return None
        return await self.session.scalar(
            select(ScamRecord)
            .join(User, User.id == ScamRecord.target_id)
            .where(
                ScamRecord.status == "ACTIVE",
                User.telegram_id.is_(None),
                User.username == username,
            )
            .order_by(ScamRecord.id.desc())
            .limit(1)
        )

    async def has_scam_identity(self, user: User) -> bool:
        target = aliased(User)
        return bool(
            await self.session.scalar(
                select(
                    select(ScamRecord.id)
                    .join(target, target.id == ScamRecord.target_id)
                    .where(
                        ScamRecord.status == "ACTIVE",
                        (ScamRecord.target_id == user.id)
                        | (
                            target.telegram_id.is_(None)
                            & target.username.is_not(None)
                            & (target.username == user.username)
                        ),
                    )
                    .exists()
                )
            )
        )

    async def trusted_candidates(
        self, offset: int, limit: int, query: str, owner_id: int | None, admin_ids: frozenset[int]
    ) -> tuple[list[User], int]:
        manual = (
            select(TrustedDesignation.target_id)
            .where(TrustedDesignation.target_id == User.id, TrustedDesignation.active.is_(True))
            .exists()
        )
        included = (
            select(TopVisibility.target_id)
            .where(TopVisibility.target_id == User.id, TopVisibility.visible.is_(True))
            .exists()
        )
        role = select(Administrator.telegram_id).where(
            Administrator.telegram_id == User.telegram_id, Administrator.active.is_(True)
        ).exists() | (
            User.telegram_id.in_(admin_ids)
            & ~select(Administrator.telegram_id)
            .where(Administrator.telegram_id == User.telegram_id)
            .exists()
        )
        if owner_id is not None:
            role = role | (User.telegram_id == owner_id)
        eligible = manual | included | role
        if query:
            search = User.username.contains(query.lower().lstrip("@"), autoescape=True)
            if query.isascii() and query.isdigit():
                search = search | (User.telegram_id == int(query))
            eligible = eligible & search
        total = await self.session.scalar(select(func.count()).select_from(User).where(eligible))
        rows = list(
            (
                await self.session.scalars(
                    select(User)
                    .where(eligible)
                    .order_by(User.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        )
        return rows, total or 0

    async def operational_stats(self) -> dict[str, int]:
        """Aggregate health counts; never return identities, report text or secrets."""
        from datetime import UTC

        result = {}
        overdue = 0
        for name, model in (("ban", BanAction), ("recovery", RecoveryDelivery)):
            result[name + "_terminal"] = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.status == "FAILED", model.attempts >= 8)
                )
                or 0
            )
            due = await self.session.scalar(
                select(
                    func.min(
                        case(
                            (model.status == "PROCESSING", model.claimed_at),
                            else_=model.next_attempt_at,
                        )
                    )
                ).where(model.status.in_(["PENDING", "FAILED", "PROCESSING"]), model.attempts < 8)
            )
            if due is not None:
                overdue = max(overdue, int((now() - due.replace(tzinfo=UTC)).total_seconds()))
        result["oldest_pending_seconds"] = max(0, overdue)
        for name, pending_model in (
            ("pending_reports", Report),
            ("pending_rep", ReputationRequest),
        ):
            result[name] = int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(pending_model)
                    .where(pending_model.status == "PENDING")
                )
                or 0
            )
        return result

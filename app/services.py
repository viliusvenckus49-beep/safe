import re
import secrets
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_services import AdminService
from app.config import Settings
from app.errors import DomainError
from app.models import (
    AuditEvent,
    ModerationAction,
    OperationLock,
    Report,
    ReportEvidence,
    ReputationAdjustment,
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
from app.repositories import Repository


class Service:
    PAGE_SIZE = 5
    USERS_PAGE_SIZE = 100

    def __init__(self, settings: Settings, session: AsyncSession):
        self.settings = settings
        self.session = session
        self.repo = Repository(session)
        self.access = AdminService(settings, session)

    async def language(self, actor: int) -> str | None:
        return await self.repo.language(actor)

    async def set_language(self, actor: int, language: str) -> None:
        if language not in {"lt", "en", "ru"}:
            raise DomainError("invalid_language")
        user = await self.repo.user_by_telegram(actor)
        if user is None:
            await self.observe(actor, None, "Vartotojas")
        await self.repo.set_language(actor, language)
        await self.session.commit()

    async def is_admin(self, actor: int) -> bool:
        return await self.access.is_admin(actor)

    async def require_admin(self, actor: int) -> None:
        await self.access.require_admin(actor)

    @staticmethod
    def _reason(value: str) -> str:
        value = value.strip()
        if not 10 <= len(value) <= 1500:
            raise DomainError("invalid_reason")
        return value

    async def observe(
        self, tg_id: int, username: str | None, name: str, *, _retried: bool = False
    ) -> User:
        if not 0 < tg_id <= 9223372036854775807:
            raise DomainError("invalid_target")
        username = username.lower().lstrip("@") if username else None
        await self.repo.lock_identity_metadata()
        user = await self.repo.user_by_telegram(tg_id)
        if user is None:
            user = User(telegram_id=tg_id, display_name=name[:256], username=username)
            self.session.add(user)
            try:
                await self.session.flush()
                self.session.add(OperationLock(user_id=user.id))
                await self.session.flush()
            except IntegrityError:
                await self.session.rollback()
                if _retried:
                    raise
                return await self.observe(tg_id, username, name, _retried=True)
        elif user.username != username:
            user.username = username
        user.display_name = name[:256]
        if username:
            previous = await self.repo.latest_username(user.id)
            if previous is None or previous.username != username:
                self.session.add(UsernameHistory(user_id=user.id, username=username))
        # A username can be reassigned; current verified ownership replaces older metadata.
        if username:
            await self.repo.retire_other_username_owners(username, user.id)
        # A trusted numeric observation may complete an existing ID association.
        # Never transfer a username-only judgment to a newly observed account.
        record = await self.repo.active_scam(user.id)
        if record is not None:
            from app.group_services import enqueue_scam_bans

            await enqueue_scam_bans(self.session, record)
        await self.session.commit()
        return user

    async def resolve(self, identifier: str, *, _retried: bool = False) -> User:
        identifier = identifier.strip()
        if not identifier or len(identifier) > 64:
            raise DomainError("invalid_target")
        await self.repo.lock_identity_metadata()
        if identifier.startswith("u:"):
            raw_id = identifier[2:]
            if not raw_id.isdigit() or not 0 < int(raw_id) <= 9223372036854775807:
                raise DomainError("invalid_target")
            user = await self.repo.user_by_id(int(raw_id))
            if user is None:
                raise DomainError("not_found")
        elif identifier.isdigit():
            tg_id = int(identifier)
            if not 0 < tg_id <= 9223372036854775807:
                raise DomainError("invalid_target")
            user = await self.repo.user_by_telegram(tg_id)
            if user is None:
                user = User(telegram_id=tg_id)
                self.session.add(user)
                try:
                    await self.session.flush()
                    self.session.add(OperationLock(user_id=user.id))
                    await self.session.flush()
                except IntegrityError:
                    await self.session.rollback()
                    if _retried:
                        raise
                    return await self.resolve(identifier, _retried=True)
        else:
            username = identifier.removeprefix("@").lower()
            if not re.fullmatch(r"[a-z][a-z0-9_]{4,31}", username):
                raise DomainError("invalid_target")
            user = await self.repo.user_by_username(username)
            if user is None:
                user = User(username=username, display_name="@" + username)
                self.session.add(user)
                try:
                    await self.session.flush()
                    self.session.add(OperationLock(user_id=user.id))
                    await self.session.flush()
                except IntegrityError:
                    await self.session.rollback()
                    if _retried:
                        raise
                    return await self.resolve(identifier, _retried=True)
        await self.session.commit()
        return user

    async def _actor(self, actor: int) -> User:
        return await self.resolve(str(actor))

    async def _lock(self, user: User) -> OperationLock:
        lock = await self.repo.lock_user(user.id)
        if lock is None:
            lock = OperationLock(user_id=user.id)
            self.session.add(lock)
            await self.session.flush()
        return lock

    async def _admin_write_lock(self, actor: int) -> None:
        """Serialize privileged writes with owner revocations and refresh authority."""
        await self.repo.lock_identity_metadata()
        await self.require_admin(actor)

    @staticmethod
    def _recent(timestamp: datetime | None, seconds: int) -> bool:
        if timestamp is None:
            return False
        return (now() - timestamp.replace(tzinfo=UTC)).total_seconds() < seconds

    async def profile(self, target: str) -> dict:
        return await self._profile_user(await self.resolve(target))

    async def _profile_user(self, user: User) -> dict:
        score, positive, negative = await self.repo.rep_stats(user.id)
        scam = await self.repo.active_scam(user.id)
        manual = await self.repo.trusted_designation(user.id)
        in_top = any(
            row["user"].id == user.id
            for row in await self.repo.leaderboard_entries(
                owner_id=self.settings.group_owner, admin_ids=self.settings.admins
            )
        )
        role = None
        if user.telegram_id is not None:
            if self.access.is_owner(user.telegram_id):
                role = "founder"
            elif await self.access.is_admin(user.telegram_id):
                role = "moderator"
        trusted = not await self.repo.has_scam_identity(user) and (
            bool(manual and manual.active) or in_top or role is not None
        )
        return dict(
            user=user,
            score=score,
            positive=positive,
            negative=negative,
            scam=scam,
            unresolved_scam=(
                await self.repo.unresolved_scam(user.username)
                if scam is None and user.telegram_id is not None
                else None
            ),
            trusted=trusted,
            role=role,
            trusted_source="manual"
            if trusted and manual and manual.active
            else "role"
            if trusted and role is not None
            else "top"
            if trusted
            else None,
            trusted_updated_at=manual.updated_at if trusted and manual and manual.active else None,
            top_visibility=(
                visibility.visible
                if (visibility := await self.repo.top_visibility(user.id)) is not None
                else None
            ),
        )

    async def vote(
        self,
        actor: int,
        target: str,
        value: int,
        chat_id: int | None,
        request_key: str,
        *,
        comment: str | None = None,
    ) -> dict:
        comment = self.validate_rep_comment(comment)
        if value not in (-1, 1) or not request_key or len(request_key) > 128:
            raise DomainError("invalid_input")
        giver, receiver = await self._actor(actor), await self.resolve(target)
        if giver.id == receiver.id:
            raise DomainError("self_rep")
        try:
            locks = {}
            for participant in sorted((giver, receiver), key=lambda item: item.id):
                locks[participant.id] = await self._lock(participant)
            lock = locks[giver.id]
            previous = await self.repo.vote_by_request(request_key)
            if previous:
                if (
                    previous.giver_user_id != giver.id
                    or previous.receiver_user_id != receiver.id
                    or previous.value != value
                    or (isinstance(previous, ReputationRequest) and previous.comment != comment)
                ):
                    raise DomainError("invalid_input")
                await self.session.commit()
                result = await self._profile_user(receiver)
                result["reputation_request"] = (
                    previous if isinstance(previous, ReputationRequest) else None
                )
                return result
            pair = await self.repo.vote_by_pair(giver.id, receiver.id)
            if pair:
                raise DomainError("duplicate_rep")
            reciprocal = await self.repo.vote_by_pair(receiver.id, giver.id)
            if reciprocal and self._recent(reciprocal.created_at, 7 * 86400):
                raise DomainError("reciprocal_rep")
            if self._recent(lock.last_rep, self.settings.rep_cooldown_seconds):
                raise DomainError("rep_cooldown")
            request = ReputationRequest(
                reference="TMP-" + secrets.token_hex(12),
                giver_user_id=giver.id,
                receiver_user_id=receiver.id,
                value=value,
                chat_id=chat_id,
                request_key=request_key,
                comment=comment,
            )
            self.session.add(request)
            await self.session.flush()
            request.reference = f"RP-{now().year}-{request.id:06d}"
            lock.last_rep = now()
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DomainError("duplicate_rep") from exc
        except DomainError:
            await self.session.rollback()
            raise
        result = await self._profile_user(receiver)
        result["reputation_request"] = request
        return result

    @staticmethod
    def validate_rep_comment(comment: str | None) -> str:
        from unicodedata import category

        if not isinstance(comment, str) or len(comment) > 1500:
            raise DomainError("rep_comment")
        comment = " ".join(comment.split())
        comment = "".join(c for c in comment if not category(c).startswith("C")).strip()
        if len("".join(c for c in comment if not c.isspace())) < 5:
            raise DomainError("rep_comment")
        return comment

    async def submit_report(
        self, actor: int, target: str, reason: str, evidence: list[dict], request_key: str
    ) -> Report:
        reason = self._reason(reason)
        if not request_key or len(request_key) > 128 or len(evidence) > 10:
            raise DomainError("invalid_input")
        for item in evidence:
            if (
                item.get("kind") not in ("photo", "document", "message")
                or not isinstance(item.get("file_id"), str)
                or not 1 <= len(item["file_id"]) <= 512
                or len(item.get("caption") or "") > 512
            ):
                raise DomainError("invalid_evidence")
        reporter, user = await self._actor(actor), await self.resolve(target)
        if reporter.id == user.id:
            raise DomainError("self_report")
        try:
            lock = await self._lock(reporter)
            report = await self.repo.report_by_request(request_key)
            if report:
                if report.reporter_id != reporter.id or report.target_id != user.id:
                    raise DomainError("invalid_input")
                await self.session.commit()
                return report
            if self._recent(lock.last_report, self.settings.report_cooldown_seconds):
                raise DomainError("report_cooldown")
            duplicate = await self.repo.pending_pair(reporter.id, user.id)
            if duplicate:
                raise DomainError("duplicate_report")
            report = Report(
                reference="TMP-" + secrets.token_hex(12),
                reporter_id=reporter.id,
                target_id=user.id,
                reason=reason,
                request_key=request_key,
            )
            self.session.add(report)
            await self.session.flush()
            report.reference = f"SC-{now().year}-{report.id:06d}"
            for item in evidence:
                self.session.add(
                    ReportEvidence(
                        report_id=report.id,
                        kind=item["kind"],
                        file_id=item["file_id"],
                        caption=item.get("caption"),
                    )
                )
            lock.last_report = now()
            await self.session.commit()
            return report
        except IntegrityError as exc:
            await self.session.rollback()
            raise DomainError("duplicate_report") from exc
        except DomainError:
            await self.session.rollback()
            raise

    def _audit(self, actor: int, action: str, target: int | None, **details) -> None:
        self.session.add(
            AuditEvent(actor_id=actor, action=action, target_id=target, details=details)
        )

    async def add_scam(self, actor: int, target: str, reason: str = "") -> ScamRecord:
        await self.require_admin(actor)
        reason = self._reason(reason) if reason.strip() else ""
        user = await self.resolve(target)
        try:
            await self._admin_write_lock(actor)
            await self._lock(user)
            if await self.repo.active_scam(user.id):
                raise DomainError("duplicate_scam")
            record = ScamRecord(
                target=user,
                target_id=user.id,
                username_snapshot=user.username,
                reason=reason,
                moderator_id=actor,
            )
            self.session.add(record)
            self._audit(actor, "scam_added", user.id)
            await self.session.flush()
            from app.group_services import enqueue_scam_bans

            await enqueue_scam_bans(self.session, record)
            await self.session.commit()
            return record
        except IntegrityError as exc:
            await self.session.rollback()
            raise DomainError("duplicate_scam") from exc
        except DomainError:
            await self.session.rollback()
            raise

    async def remove_scam(self, actor: int, target: str, reason: str) -> bool:
        await self.require_admin(actor)
        reason = self._reason(reason)
        user = await self.resolve(target)
        await self._admin_write_lock(actor)
        await self._lock(user)
        record = await self.repo.active_scam(user.id)
        if record is None:
            await self.session.commit()
            return False
        record.status, record.removed_at, record.removed_by, record.removal_reason = (
            "REMOVED",
            now(),
            actor,
            reason,
        )
        self._audit(actor, "scam_removed", user.id, reason=reason)
        await self.session.commit()
        return True

    async def moderate(self, actor: int, reference: str, approve: bool, notes: str = "") -> Report:
        await self.require_admin(actor)
        if len(notes) > 2000:
            raise DomainError("invalid_input")
        # Serialize all decisions for the target before refreshing the report status.
        report = await self.repo.report_by_reference(reference)
        if report is None:
            raise DomainError("not_found")
        user = await self.repo.user_by_id(report.target_id)
        if user is None:
            raise DomainError("not_found")
        await self._admin_write_lock(actor)
        await self._lock(user)
        await self.session.refresh(report)
        decision = "APPROVED" if approve else "REJECTED"
        if report.status != "PENDING":
            await self.session.commit()
            if report.status != decision:
                raise DomainError("already_moderated")
            return report
        report.status = decision
        if approve:
            record = await self.repo.active_scam(report.target_id)
            if record is None:
                record = ScamRecord(
                    target_id=user.id,
                    username_snapshot=user.username,
                    reason=report.reason,
                    moderator_id=actor,
                    report_id=report.id,
                )
                self.session.add(record)
                await self.session.flush()
            from app.group_services import enqueue_scam_bans

            await enqueue_scam_bans(self.session, record)
        self.session.add(
            ModerationAction(
                report_id=report.id, moderator_id=actor, decision=decision, notes=notes
            )
        )
        self._audit(
            actor, "report_" + decision.lower(), report.target_id, reference=reference, notes=notes
        )
        await self.session.commit()
        return report

    async def leaderboard(self) -> list[dict]:
        return await self.repo.leaderboard_entries(
            owner_id=self.settings.group_owner, admin_ids=self.settings.admins
        )

    @staticmethod
    def _page(page: int) -> int:
        if not isinstance(page, int) or not 0 <= page <= 100000:
            raise DomainError("invalid_input")
        return page * Service.PAGE_SIZE

    async def scams(self, page: int) -> tuple[list[ScamRecord], int]:
        return await self.repo.scams(self._page(page), self.PAGE_SIZE)

    async def pending(self, actor: int, page: int) -> tuple[list[Report], int]:
        await self.require_admin(actor)
        return await self.repo.pending(self._page(page), self.PAGE_SIZE)

    async def report_details(self, actor: int, reference: str) -> dict:
        await self.require_admin(actor)
        report = await self.repo.report_by_reference(reference, details=True)
        if report is None:
            raise DomainError("not_found")
        return dict(
            report=report, target=report.target, reporter=report.reporter, evidence=report.evidence
        )

    async def admin_stats(self, actor: int) -> dict:
        await self.require_admin(actor)
        return await self.repo.admin_stats()

    async def audits(self, actor: int, page: int) -> list[AuditEvent]:
        await self.require_admin(actor)
        return await self.repo.audits(self._page(page), self.PAGE_SIZE)

    async def users(self, actor: int, page: int) -> tuple[list[User], int]:
        await self.require_admin(actor)
        self._page(page)  # Validate the index without changing other list sizes.
        return await self.repo.users(page * self.USERS_PAGE_SIZE, self.USERS_PAGE_SIZE)

    async def pending_next(self, actor: int, reference: str | None) -> Report | None:
        await self.require_admin(actor)
        return await self.repo.pending_next(reference)

    async def audit_page(self, actor: int, page: int) -> tuple[list[AuditEvent], int]:
        await self.require_admin(actor)
        rows = await self.repo.audits(self._page(page), self.PAGE_SIZE)
        return rows, await self.repo.audit_count()

    async def pending_reputation(
        self, actor: int, page: int = 0
    ) -> tuple[list[ReputationRequest], int]:
        await self.require_admin(actor)
        return await self.repo.pending_reputation(self._page(page), self.PAGE_SIZE)

    async def reputation_details(self, actor: int, reference: str) -> ReputationRequest:
        await self.require_admin(actor)
        request = await self.repo.reputation_request(reference)
        if request is None:
            raise DomainError("not_found")
        return request

    async def moderate_reputation(
        self, actor: int, reference: str, approve: bool, notes: str = ""
    ) -> ReputationRequest:
        await self.require_admin(actor)
        if len(notes) > 1500:
            raise DomainError("invalid_input")
        request = await self.reputation_details(actor, reference)
        await self._admin_write_lock(actor)
        await self._lock(request.receiver)
        await self.session.refresh(request)
        decision = "APPROVED" if approve else "REJECTED"
        if request.status != "PENDING":
            await self.session.commit()
            if request.status != decision:
                raise DomainError("already_moderated")
            return request
        request.status, request.moderator_id, request.moderated_at, request.notes = (
            decision,
            actor,
            now(),
            notes,
        )
        self._audit(
            actor,
            "reputation_" + decision.lower(),
            request.receiver_user_id,
            reference=reference,
            value=request.value,
            notes=notes,
        )
        await self.session.commit()
        return await self.reputation_details(actor, reference)

    @staticmethod
    def _request_key(key: str) -> None:
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            raise DomainError("invalid_input")

    async def _adjust_rep(
        self, actor: int, target: str, value: int | None, reason: str, request_key: str
    ) -> dict:
        await self.require_admin(actor)
        self._request_key(request_key)
        reason = self._reason(reason)
        if value is not None and (
            type(value) is not int or not -10000 <= value <= 10000 or value == 0
        ):
            raise DomainError("invalid_input")
        user = await self.resolve(target)
        try:
            await self._admin_write_lock(actor)
            await self._lock(user)
            previous = await self.repo.adjustment_by_key(request_key)
            operation = "RESET" if value is None else "ADJUST"
            if previous:
                expected_positive = max(value, 0) if value is not None else previous.positive_delta
                expected_negative = max(-value, 0) if value is not None else previous.negative_delta
                if (
                    previous.actor_id != actor
                    or previous.target_id != user.id
                    or previous.operation != operation
                    or previous.reason != reason
                    or previous.positive_delta != expected_positive
                    or previous.negative_delta != expected_negative
                ):
                    raise DomainError("invalid_input")
            else:
                _, positive, negative = await self.repo.rep_stats(user.id)
                positive_delta = -positive if value is None else max(value, 0)
                negative_delta = -negative if value is None else max(-value, 0)
                self.session.add(
                    ReputationAdjustment(
                        target_id=user.id,
                        actor_id=actor,
                        positive_delta=positive_delta,
                        negative_delta=negative_delta,
                        operation=operation,
                        reason=reason,
                        request_key=request_key,
                    )
                )
                self._audit(
                    actor,
                    "reputation_" + operation.lower(),
                    user.id,
                    positive_delta=positive_delta,
                    negative_delta=negative_delta,
                    reason=reason,
                    request_key=request_key,
                )
            await self.session.commit()
        except (IntegrityError, DomainError):
            await self.session.rollback()
            raise
        return await self._profile_user(user)

    async def admin_adjust_rep(
        self, actor: int, target: str, value: int, reason: str, request_key: str
    ) -> dict:
        return await self._adjust_rep(actor, target, value, reason, request_key)

    async def admin_reset_rep(self, actor: int, target: str, reason: str, request_key: str) -> dict:
        return await self._adjust_rep(actor, target, None, reason, request_key)

    async def set_top_visibility(
        self, actor: int, target: str, visible: bool, reason: str, request_key: str
    ) -> dict:
        await self.require_admin(actor)
        self._request_key(request_key)
        reason = self._reason(reason)
        if type(visible) is not bool:
            raise DomainError("invalid_input")
        user = await self.resolve(target)
        try:
            await self._admin_write_lock(actor)
            await self._lock(user)
            previous = await self.repo.top_action_by_key(request_key)
            if previous:
                if (
                    previous.actor_id != actor
                    or previous.target_id != user.id
                    or previous.visible != visible
                    or previous.reason != reason
                ):
                    raise DomainError("invalid_input")
            else:
                visibility = await self.repo.top_visibility(user.id)
                if visibility is None:
                    self.session.add(TopVisibility(target_id=user.id, visible=visible))
                else:
                    visibility.visible = visible
                self.session.add(
                    TopVisibilityAction(
                        target_id=user.id,
                        actor_id=actor,
                        visible=visible,
                        reason=reason,
                        request_key=request_key,
                    )
                )
                self._audit(
                    actor,
                    "top_visibility",
                    user.id,
                    visible=visible,
                    reason=reason,
                    request_key=request_key,
                )
            await self.session.commit()
        except (IntegrityError, DomainError):
            await self.session.rollback()
            raise
        return await self._profile_user(user)

    async def admin_reset_all_reputation(
        self, actor: int, reason: str, request_key: str
    ) -> dict[str, int]:
        """Clear current counters and reject preexisting pending requests atomically.

        Historical events remain unchanged. Replaying the same request does not
        clear reputation earned after the original reset.
        """
        import hashlib

        await self.require_admin(actor)
        self._request_key(request_key)
        reason = self._reason(reason)
        try:
            # Identity metadata lock also coordinates global reset requests and
            # prevents first-time user insertion between snapshot and locking.
            await self._admin_write_lock(actor)
            previous = await self.repo.global_reset_by_key(request_key)
            if previous is not None:
                if previous.actor_id != actor or previous.details.get("reason") != reason:
                    raise DomainError("invalid_input")
                await self.session.commit()
                return {
                    "users_reset": int(previous.details["users_reset"]),
                    "requests_rejected": int(previous.details["requests_rejected"]),
                }
            user_ids = await self.repo.all_user_ids()
            for user_id in user_ids:
                lock = await self.repo.lock_user(user_id)
                if lock is None:
                    self.session.add(OperationLock(user_id=user_id))
                    await self.session.flush()
            reset_count = 0
            prefix = hashlib.sha256(request_key.encode()).hexdigest()
            for user_id in user_ids:
                _, positive, negative = await self.repo.rep_stats(user_id)
                if positive == 0 and negative == 0:
                    continue
                self.session.add(
                    ReputationAdjustment(
                        target_id=user_id,
                        actor_id=actor,
                        positive_delta=-positive,
                        negative_delta=-negative,
                        operation="RESET",
                        reason=reason,
                        request_key=f"global:{prefix}:{user_id}",
                    )
                )
                self._audit(
                    actor,
                    "reputation_reset",
                    user_id,
                    positive_delta=-positive,
                    negative_delta=-negative,
                    reason=reason,
                    global_request_key=request_key,
                )
                reset_count += 1
            pending = await self.repo.all_pending_reputation()
            for request in pending:
                request.status = "REJECTED"
                request.moderator_id = actor
                request.moderated_at = now()
                request.notes = reason
                self._audit(
                    actor,
                    "reputation_rejected",
                    request.receiver_user_id,
                    reference=request.reference,
                    value=request.value,
                    notes=reason,
                    global_request_key=request_key,
                )
            summary = {"users_reset": reset_count, "requests_rejected": len(pending)}
            self.session.add(
                AuditEvent(
                    actor_id=actor,
                    action="reputation_reset_all",
                    details={"request_key": request_key, "reason": reason, **summary},
                )
            )
            await self.session.commit()
            return summary
        except (DomainError, IntegrityError):
            await self.session.rollback()
            raise

    async def set_trusted(self, actor: int, target: str, active: bool, request_key: str) -> dict:
        await self.require_admin(actor)
        self._request_key(request_key)
        if type(active) is not bool:
            raise DomainError("invalid_input")
        user = await self.resolve(target)
        try:
            await self.repo.lock_identity_metadata()
            refreshed = await self.repo.user_by_id(user.id)
            if refreshed is None:
                raise DomainError("not_found")
            user = refreshed
            await self.require_admin(actor)
            previous = await self.repo.trusted_action(request_key)
            if previous:
                if (previous.actor_id, previous.target_id, previous.active) != (
                    actor,
                    user.id,
                    active,
                ):
                    raise DomainError("stale_callback")
                await self.session.commit()
                result = await self._profile_user(user)
                result["trusted_request_replayed"] = True
                return result
            if active and await self.repo.has_scam_identity(user):
                raise DomainError("trusted_scam")
            row = await self.repo.trusted_designation(user.id)
            changed = bool(row and row.active) != active
            if row is None:
                row = TrustedDesignation(target_id=user.id, active=active, actor_id=actor)
                self.session.add(row)
            else:
                row.active, row.actor_id = active, actor
            self.session.add(
                TrustedAction(
                    target_id=user.id, actor_id=actor, active=active, request_key=request_key
                )
            )
            if changed:
                self._audit(actor, "trusted_added" if active else "trusted_removed", user.id)
            await self.session.commit()
            result = await self._profile_user(user)
            result["trusted_request_replayed"] = False
            return result
        except Exception:
            await self.session.rollback()
            raise

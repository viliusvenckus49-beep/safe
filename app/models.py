from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    username: Mapped[str | None] = mapped_column(String(32), index=True)
    language: Mapped[str | None] = mapped_column(String(2), nullable=True)
    display_name: Mapped[str] = mapped_column(String(256), default="Vartotojas")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (
        CheckConstraint(
            "language IS NULL OR language IN ('lt','en','ru')", name="ck_user_language"
        ),
        Index(
            "uq_unknown_username",
            "username",
            unique=True,
            sqlite_where=text("telegram_id IS NULL"),
            postgresql_where=text("telegram_id IS NULL"),
        ),
    )


class UsernameHistory(Base):
    __tablename__ = "username_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    username: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class OperationLock(Base):
    __tablename__ = "operation_locks"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    last_rep: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_report: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReputationEvent(Base):
    __tablename__ = "reputation_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    giver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    receiver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    value: Mapped[int] = mapped_column(Integer)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("value IN (-1, 1)"),
        CheckConstraint("giver_user_id != receiver_user_id"),
        UniqueConstraint("giver_user_id", "receiver_user_id", name="uq_rep_pair"),
    )


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    target: Mapped[User] = relationship(foreign_keys=[target_id])
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])
    evidence: Mapped[list["ReportEvidence"]] = relationship(cascade="all, delete-orphan")
    __table_args__ = (CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')"),)


class ReportEvidence(Base):
    __tablename__ = "report_evidence"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    file_id: Mapped[str] = mapped_column(String(512))
    caption: Mapped[str | None] = mapped_column(String(512))
    __table_args__ = (CheckConstraint("kind IN ('photo','document','message')"),)


class ScamRecord(Base):
    __tablename__ = "scam_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    username_snapshot: Mapped[str | None] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("reports.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_by: Mapped[int | None] = mapped_column(BigInteger)
    removal_reason: Mapped[str | None] = mapped_column(Text)
    target: Mapped[User] = relationship()
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','REMOVED')"),
        Index(
            "uq_active_scam",
            "target_id",
            unique=True,
            sqlite_where=text("status='ACTIVE'"),
            postgresql_where=text("status='ACTIVE'"),
        ),
    )


class ModerationAction(Base):
    __tablename__ = "moderation_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"), unique=True)
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    decision: Mapped[str] = mapped_column(String(16))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class IdentityLock(Base):
    """A short-lived singleton lock serializes observed username ownership changes."""

    __tablename__ = "identity_lock"
    id: Mapped[int] = mapped_column(primary_key=True)
    __table_args__ = (CheckConstraint("id = 1", name="ck_identity_lock_singleton"),)


class ReputationRequest(Base):
    __tablename__ = "reputation_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), unique=True)
    giver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    receiver_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    value: Mapped[int] = mapped_column(Integer)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    moderator_id: Mapped[int | None] = mapped_column(BigInteger)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    giver: Mapped[User] = relationship(foreign_keys=[giver_user_id])
    receiver: Mapped[User] = relationship(foreign_keys=[receiver_user_id])
    __table_args__ = (
        CheckConstraint("value IN (-1,1)"),
        CheckConstraint("giver_user_id != receiver_user_id"),
        CheckConstraint("status IN ('PENDING','APPROVED','REJECTED')"),
        UniqueConstraint("giver_user_id", "receiver_user_id", name="uq_rep_request_pair"),
        CheckConstraint(
            "comment IS NULL OR length(trim(comment)) BETWEEN 5 AND 1500",
            name="ck_rep_comment",
        ),
    )


class ReputationAdjustment(Base):
    __tablename__ = "reputation_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    positive_delta: Mapped[int] = mapped_column(Integer)
    negative_delta: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (CheckConstraint("operation IN ('ADJUST','RESET')"),)


class TopVisibility(Base):
    __tablename__ = "top_visibility"
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    visible: Mapped[bool] = mapped_column()


class TopVisibilityAction(Base):
    __tablename__ = "top_visibility_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    visible: Mapped[bool] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ManagedGroup(Base):
    __tablename__ = "managed_groups"
    chat_type: Mapped[str] = mapped_column(String(16), default="supergroup")
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    approved: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(default=True)
    can_restrict_members: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ObservedMember(Base):
    __tablename__ = "observed_members"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(256))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PrivateContact(Base):
    __tablename__ = "private_contacts"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecoverySubscription(Base):
    __tablename__ = "recovery_subscriptions"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(
        ForeignKey("private_contacts.telegram_id"), primary_key=True
    )
    consent: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class BanAction(Base):
    __tablename__ = "ban_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    scam_record_id: Mapped[int] = mapped_column(ForeignKey("scam_records.id"))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_type: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_id", "scam_record_id", name="uq_ban_activation"),
        CheckConstraint("status IN ('PENDING','PROCESSING','SUCCEEDED','FAILED','OBSOLETE')"),
    )


class RecoveryCampaign(Base):
    __tablename__ = "recovery_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    invite_url: Mapped[str] = mapped_column(Text)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class RecoveryDelivery(Base):
    __tablename__ = "recovery_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("recovery_campaigns.id"))
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_type: Mapped[str | None] = mapped_column(String(64))
    campaign: Mapped[RecoveryCampaign] = relationship()
    __table_args__ = (
        UniqueConstraint("campaign_id", "telegram_id", name="uq_recovery_recipient"),
        CheckConstraint("status IN ('PENDING','PROCESSING','SUCCEEDED','FAILED','OBSOLETE')"),
    )


class Administrator(Base):
    """Explicit grants/revocations override bootstrap configuration; owner is immutable."""

    __tablename__ = "administrators"
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    active: Mapped[bool] = mapped_column(default=True)
    assigned_by: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    __table_args__ = (CheckConstraint("telegram_id > 0", name="ck_administrator_id"),)


class AdministratorChange(Base):
    __tablename__ = "administrator_changes"
    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(ForeignKey("administrators.telegram_id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column()
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class TrustedDesignation(Base):
    __tablename__ = "trusted_designations"
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    active: Mapped[bool] = mapped_column()
    actor_id: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class TrustedAction(Base):
    __tablename__ = "trusted_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column()
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

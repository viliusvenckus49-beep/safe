from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
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


class ProfileActivity(Base):
    """Actual ordinary messages only; persistent chat/message deduplication."""

    __tablename__ = "profile_activity"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    active_date: Mapped[date] = mapped_column(Date)


class GroupModerationSettings(Base):
    """Local moderation is opt-in; global SAFECheck administrator grants do not apply."""

    __tablename__ = "group_moderation_settings"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    bans_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    mutes_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    warnings_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    kicks_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    allow_permanent_mutes: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    max_duration_seconds: Mapped[int] = mapped_column(default=2592000, server_default="2592000")
    allow_permanent_bans: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_group_moderation_version"),
        CheckConstraint(
            "max_duration_seconds BETWEEN 60 AND 31536000", name="ck_group_moderation_duration"
        ),
    )


class GroupSanction(Base):
    """One live local restriction per group/user; never a SCAM or report judgment."""

    __tablename__ = "group_sanctions"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"))
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(1000))
    until_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    owned: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    policy_version: Mapped[int] = mapped_column()
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    revoke_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("telegram_id > 0 AND actor_id > 0", name="ck_group_sanction_ids"),
        CheckConstraint("kind IN ('BAN','MUTE','WARN','KICK')", name="ck_group_sanction_kind"),
        CheckConstraint(
            "status IN ('PENDING','ACTIVE','REVOKING','UNCERTAIN','REVOKED','EXPIRED','CANCELLED','FAILED','RECORDED','COMPLETED')",
            name="ck_group_sanction_status",
        ),
        Index(
            "uq_group_live_sanction",
            "chat_id",
            "telegram_id",
            unique=True,
            sqlite_where=text("status IN ('PENDING','ACTIVE','REVOKING','UNCERTAIN')"),
            postgresql_where=text("status IN ('PENDING','ACTIVE','REVOKING','UNCERTAIN')"),
        ),
    )


class GroupSanctionJob(Base):
    """Fenced durable work; ambiguous writes are retained for reconciliation."""

    __tablename__ = "group_sanction_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    sanction_id: Mapped[int] = mapped_column(ForeignKey("group_sanctions.id"))
    operation: Mapped[str] = mapped_column(String(8))
    actor_id: Mapped[int | None] = mapped_column(BigInteger)
    automatic: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), default="PENDING", server_default="PENDING")
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[str | None] = mapped_column(String(32))
    result: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("sanction_id", "operation", name="uq_group_sanction_operation"),
        CheckConstraint("operation IN ('APPLY','RELEASE')", name="ck_group_job_operation"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','SUCCEEDED','FAILED','CANCELLED','UNCERTAIN')",
            name="ck_group_job_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_group_job_attempts"),
        Index("ix_group_job_due", "status", "available_at"),
        Index("ix_group_job_lease", "status", "lease_until"),
    )


class GroupModerationDraft(Base):
    """Durable actor-bound confirmation; never store untrusted usernames as targets."""

    __tablename__ = "group_moderation_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(1000), default="")
    until_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sanction_id: Mapped[int | None] = mapped_column(ForeignKey("group_sanctions.id"))
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    panel_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    panel_message_id: Mapped[int | None] = mapped_column(BigInteger)
    language: Mapped[str] = mapped_column(String(2), default="lt")
    receipt_hash: Mapped[str | None] = mapped_column(String(64))
    receipt_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_done: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    __table_args__ = (
        CheckConstraint("actor_id > 0 AND telegram_id > 0", name="ck_group_draft_ids"),
        CheckConstraint(
            "status IN ('PENDING','CONFIRMED','CANCELLED')", name="ck_group_draft_status"
        ),
        CheckConstraint(
            "kind IN ('BAN','MUTE','WARN','KICK','UNBAN','UNMUTE')", name="ck_group_draft_kind"
        ),
        Index("ix_group_draft_expiry", "status", "expires_at"),
    )


class AdCampaign(Base):
    """Stage 3.1 campaigns are drafts only; content never stores publishing settings."""

    __tablename__ = "ad_campaigns"
    id: Mapped[int] = mapped_column(primary_key=True)
    internal_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", server_default="DRAFT")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_by: Mapped[int] = mapped_column(BigInteger)
    updated_by: Mapped[int] = mapped_column(BigInteger)
    client_telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ARCHIVED')", name="ck_ad_campaign_status"),
        CheckConstraint(
            "version > 0 AND created_by > 0 AND updated_by > 0", name="ck_ad_campaign_ids"
        ),
        CheckConstraint(
            "client_telegram_id IS NULL OR client_telegram_id > 0", name="ck_ad_client_id"
        ),
    )


class AdContentVersion(Base):
    __tablename__ = "ad_content_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    revision: Mapped[int] = mapped_column()
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("campaign_id", "revision", name="uq_ad_content_revision"),
        CheckConstraint("revision > 0 AND actor_id > 0", name="ck_ad_content_ids"),
    )


class AdContentDraft(Base):
    __tablename__ = "ad_content_drafts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    base_version: Mapped[int] = mapped_column()
    field: Mapped[str] = mapped_column(String(12))
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    inputs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="READY")
    previewed: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    saved_revision: Mapped[int | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('COLLECTING','READY','SAVED','CANCELLED')", name="ck_ad_draft_status"
        ),
        CheckConstraint("field IN ('TEXT','MEDIA','BUTTONS','RESTORE')", name="ck_ad_draft_field"),
        CheckConstraint("actor_id > 0 AND base_version > 0", name="ck_ad_draft_ids"),
        Index("ix_ad_draft_owner", "actor_id", "status", "expires_at"),
    )


class AdSchedule(Base):
    __tablename__ = "ad_schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    chat_id: Mapped[int | None] = mapped_column(ForeignKey("managed_groups.chat_id"))
    status: Mapped[str] = mapped_column(String(12), default="DRAFT", server_default="DRAFT")
    interval_seconds: Mapped[int | None] = mapped_column()
    start_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[str | None] = mapped_column(String(12))
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    authorized_by: Mapped[int] = mapped_column(BigInteger)
    activated_revision: Mapped[int | None] = mapped_column()
    activation_base: Mapped[int | None] = mapped_column()
    last_request: Mapped[str | None] = mapped_column(String(128))
    last_payload: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(512))
    alert_pending: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        UniqueConstraint("campaign_id", "chat_id", name="uq_ad_schedule_group"),
        CheckConstraint("chat_id IS NULL OR chat_id < 0", name="ck_ad_schedule_chat"),
        CheckConstraint(
            "status != 'ACTIVE' OR (chat_id IS NOT NULL AND interval_seconds IS NOT NULL AND start_at IS NOT NULL AND next_run_at IS NOT NULL)",
            name="ck_ad_schedule_active",
        ),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','STOPPED','EXPIRED')",
            name="ck_ad_schedule_status",
        ),
        CheckConstraint(
            "interval_seconds IS NULL OR interval_seconds IN (1800,3600,7200,10800,14400,21600,28800,43200,86400)",
            name="ck_ad_schedule_interval",
        ),
        CheckConstraint("version > 0 AND authorized_by > 0", name="ck_ad_schedule_ids"),
        CheckConstraint(
            "end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="ck_ad_schedule_dates"
        ),
    )


class AdPublicationAttempt(Base):
    __tablename__ = "ad_publication_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("ad_schedules.id"))
    content_version_id: Mapped[int] = mapped_column(ForeignKey("ad_content_versions.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    schedule_version: Mapped[int] = mapped_column()
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(12), default="PENDING")
    token: Mapped[str | None] = mapped_column(String(32))
    fence: Mapped[int] = mapped_column(default=0, server_default="0")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(512))
    notified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("schedule_id", "due_at", name="uq_ad_publication_slot"),
        CheckConstraint(
            "status NOT IN ('CLAIMED','SENDING') OR (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_ad_attempt_lease",
        ),
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','SENDING','SENT','FAILED','DEFERRED','UNCERTAIN','CANCELLED')",
            name="ck_ad_attempt_status",
        ),
        CheckConstraint(
            "fence >= 0 AND schedule_version > 0 AND chat_id < 0", name="ck_ad_attempt_ids"
        ),
        Index("ix_ad_attempt_work", "status", "retry_at", "lease_until"),
    )


class AdPublicationPart(Base):
    __tablename__ = "ad_publication_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("ad_publication_attempts.id"))
    position: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(12), default="PENDING")
    message_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    tries: Mapped[int] = mapped_column(default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(String(512))
    __table_args__ = (
        UniqueConstraint("attempt_id", "position", name="uq_ad_publication_part"),
        CheckConstraint("position >= 0 AND tries >= 0", name="ck_ad_part_numbers"),
        CheckConstraint(
            "kind IN ('TEXT','PHOTO','VIDEO','ALBUM','BUTTONS')", name="ck_ad_part_kind"
        ),
        CheckConstraint(
            "status IN ('PENDING','SENDING','SENT','FAILED','DEFERRED','UNCERTAIN','CANCELLED')",
            name="ck_ad_part_status",
        ),
    )


class AdPublicationGate(Base):
    """Shared durable ADS FloodWait; stopping one campaign cannot remove the delay."""

    __tablename__ = "ad_publication_gate"
    id: Mapped[int] = mapped_column(primary_key=True)
    until_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("id = 1", name="ck_ad_gate_singleton"),)

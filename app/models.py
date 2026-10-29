from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
    """Local commands require live Telegram authority; global administrator grants do not apply."""

    __tablename__ = "group_moderation_settings"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    local_auto_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    bans_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    mutes_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    warnings_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    kicks_enabled: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    allow_permanent_mutes: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    max_duration_seconds: Mapped[int] = mapped_column(default=2592000, server_default="2592000")
    allow_permanent_bans: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    warning_limit: Mapped[int] = mapped_column(default=3, server_default="3")
    warning_action: Mapped[str] = mapped_column(String(4), default="MUTE", server_default="MUTE")
    warning_mute_seconds: Mapped[int] = mapped_column(default=86400, server_default="86400")
    warning_version: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_by: Mapped[int | None] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_group_moderation_version"),
        CheckConstraint("warning_limit BETWEEN 2 AND 6", name="ck_group_warning_limit"),
        CheckConstraint(
            "warning_action IN ('OFF','KICK','MUTE','BAN')", name="ck_group_warning_action"
        ),
        CheckConstraint(
            "warning_mute_seconds BETWEEN 60 AND 31536000", name="ck_group_warning_mute"
        ),
        CheckConstraint("warning_version > 0", name="ck_group_warning_version"),
        CheckConstraint(
            "max_duration_seconds BETWEEN 60 AND 31536000", name="ck_group_moderation_duration"
        ),
    )


class GroupCheckSettings(Base):
    __tablename__ = "group_check_settings"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    actions: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    check_on_join: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    delete_messages: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    notify_result: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    mute_seconds: Mapped[int] = mapped_column(default=86400, server_default="86400")
    spam_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    banned_words: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    words_action: Mapped[str] = mapped_column(String(8), default="OFF", server_default="OFF")
    words_delete: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    language: Mapped[str] = mapped_column(String(2), default="lt", server_default="lt")
    updated_by: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("version>0", name="ck_check_version"),
        CheckConstraint("mute_seconds BETWEEN 300 AND 2592000", name="ck_check_duration"),
        CheckConstraint("language IN ('lt','en','ru')", name="ck_check_language"),
    )


class GroupCheckMember(Base):
    __tablename__ = "group_check_members"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    episodes: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    kick_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    kick_join: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupCheckEvent(Base):
    """Durable rule episodes and fenced, non-replayed advice/deletion deliveries."""

    __tablename__ = "group_check_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String(256), default="")
    rule: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(64), unique=True)
    state: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    sanction_id: Mapped[int | None] = mapped_column(ForeignKey("group_sanctions.id"))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    notice_message_id: Mapped[int | None] = mapped_column(BigInteger)
    notice_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execute_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_seconds: Mapped[int] = mapped_column(default=15, server_default="15")
    kick_attempt: Mapped[int] = mapped_column(default=0, server_default="0")
    matched_word: Mapped[str | None] = mapped_column(String(100))
    message_deleted: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    result: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class GroupSanction(Base):
    """Local restrictions; an owned MUTE may be replaced by a linked BAN/KICK."""

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
    replaces_sanction_id: Mapped[int | None] = mapped_column(
        ForeignKey("group_sanctions.id", name="fk_sanction_replaces"), nullable=True
    )
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
            "kind",
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
    receipt_visibility: Mapped[str | None] = mapped_column(String(8))
    original_command: Mapped[str | None] = mapped_column(String(64))
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


class GroupWarningEscalation(Base):
    """One durable, group-scoped escalation per triggering warning."""

    __tablename__ = "group_warning_escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"))
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    trigger_id: Mapped[int] = mapped_column(ForeignKey("group_sanctions.id"), unique=True)
    sanction_id: Mapped[int | None] = mapped_column(ForeignKey("group_sanctions.id"), unique=True)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("group_moderation_drafts.id"))
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    warning_limit: Mapped[int] = mapped_column(default=3, server_default="3")
    result: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("telegram_id > 0", name="ck_warning_escalation_user"),
        CheckConstraint("warning_limit BETWEEN 2 AND 6", name="ck_escalation_warning_limit"),
        CheckConstraint(
            "state IN ('PENDING','SUCCEEDED','FAILED','UNCERTAIN','CANCELLED')",
            name="ck_warning_escalation_state",
        ),
        Index("ix_warning_escalation_target", "chat_id", "telegram_id", "state"),
    )


class GroupWarningReset(Base):
    """Expiring confirmation bound to an actor, public receipt and warning set."""

    __tablename__ = "group_warning_resets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    origin_id: Mapped[int] = mapped_column(ForeignKey("group_sanctions.id"))
    selected_warning_id: Mapped[int | None] = mapped_column(ForeignKey("group_sanctions.id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    max_warning_id: Mapped[int] = mapped_column()
    expected_count: Mapped[int] = mapped_column()
    state: Mapped[str] = mapped_column(String(16), default="PENDING")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint("actor_id > 0 AND expected_count > 0", name="ck_warning_reset_actor_count"),
        CheckConstraint(
            "state IN ('PENDING','CONFIRMED','CANCELLED')", name="ck_warning_reset_state"
        ),
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
    excluded: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    override_fields: Mapped[list[str]] = mapped_column(
        JSON,
        default=lambda: ["interval_seconds", "start_at", "end_at"],
        server_default=text('\'["interval_seconds","start_at","end_at"]\''),
    )
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index("ix_ad_schedule_due", "status", "next_run_at", "id"),
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
        Index("ix_ad_attempt_schedule_status", "schedule_id", "status", "id"),
    )


class AdPublicationPart(Base):
    __tablename__ = "ad_publication_parts"
    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("ad_publication_attempts.id"))
    position: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(9))
    status: Mapped[str] = mapped_column(String(12), default="PENDING")
    message_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    tries: Mapped[int] = mapped_column(default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(String(512))
    __table_args__ = (
        UniqueConstraint("attempt_id", "position", name="uq_ad_publication_part"),
        CheckConstraint("position >= 0 AND tries >= 0", name="ck_ad_part_numbers"),
        CheckConstraint(
            "kind IN ('TEXT','PHOTO','VIDEO','ANIMATION','DOCUMENT','ALBUM','BUTTONS')",
            name="ck_ad_part_kind",
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
    next_send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("id = 1", name="ck_ad_gate_singleton"),)


class AdAssignmentPolicy(Base):
    __tablename__ = "ad_assignment_policies"
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(10), default="SINGLE", server_default="SINGLE")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    interval_seconds: Mapped[int] = mapped_column(default=3600, server_default="3600")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auto_activate: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    authorized_by: Mapped[int] = mapped_column(BigInteger)
    scan_after: Mapped[int | None] = mapped_column(BigInteger)
    reconcile_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(512))
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (
        CheckConstraint(
            "mode IN ('SINGLE','SELECTED','EXISTING','FUTURE')", name="ck_ad_assignment_mode"
        ),
        CheckConstraint("version > 0 AND authorized_by > 0", name="ck_ad_assignment_ids"),
        CheckConstraint(
            "interval_seconds IN (1800,3600,7200,10800,14400,21600,28800,43200,86400)",
            name="ck_ad_assignment_interval",
        ),
        CheckConstraint(
            "end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="ck_ad_assignment_dates"
        ),
        Index("ix_ad_assignment_reconcile", "mode", "reconcile_at", "campaign_id"),
    )


class AdGlobalOperation(Base):
    __tablename__ = "ad_global_operations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="SELECTING")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    policy_version: Mapped[int] = mapped_column()
    content_revision: Mapped[int | None] = mapped_column()
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('SELECTING','READY','RUNNING','DONE','CANCELLED')",
            name="ck_ad_global_status",
        ),
        CheckConstraint(
            "action IN ('ASSIGN','START','PAUSE','STOP','REMOVE','DEFAULTS','INHERIT','CHANGE','FUTURE_ON','FUTURE_OFF')",
            name="ck_ad_global_action",
        ),
        CheckConstraint(
            "actor_id > 0 AND version > 0 AND policy_version > 0", name="ck_ad_global_ids"
        ),
        Index("ix_ad_global_resume", "status", "created_at"),
    )


class AdGlobalOperationItem(Base):
    __tablename__ = "ad_global_operation_items"
    operation_id: Mapped[str] = mapped_column(
        ForeignKey("ad_global_operations.id"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("ad_schedules.id"))
    expected_version: Mapped[int | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(16), default="ASSIGN", server_default="ASSIGN")
    status: Mapped[str] = mapped_column(String(12), default="PENDING")
    token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(512))
    __table_args__ = (
        CheckConstraint("chat_id < 0", name="ck_ad_global_item_chat"),
        CheckConstraint(
            "status IN ('PENDING','CLAIMED','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_ad_global_item_status",
        ),
        CheckConstraint(
            "status != 'CLAIMED' OR (token IS NOT NULL AND lease_until IS NOT NULL)",
            name="ck_ad_global_item_lease",
        ),
        Index("ix_ad_global_item_work", "operation_id", "status", "lease_until"),
    )


class AdChatPublicationGate(Base):
    __tablename__ = "ad_chat_publication_gates"
    chat_id: Mapped[int] = mapped_column(ForeignKey("managed_groups.chat_id"), primary_key=True)
    next_send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("chat_id < 0", name="ck_ad_chat_gate_id"),)


class AdCustomerGrant(Base):
    """Retained assignments; revocation never resurrects an older grant."""

    __tablename__ = "ad_customer_grants"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    customer_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    editing_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_by: Mapped[int | None] = mapped_column(BigInteger)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "customer_id > 0 AND assigned_by > 0 AND version > 0", name="ck_ad_grant_ids"
        ),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by > 0)",
            name="ck_ad_grant_status",
        ),
        UniqueConstraint("id", "campaign_id", "customer_id", name="uq_ad_grant_identity"),
        Index(
            "uq_ad_active_customer",
            "campaign_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index("ix_ad_customer_campaigns", "customer_id", "status", "campaign_id"),
        Index("ix_ad_grant_campaign", "campaign_id", "id"),
    )


class AdCustomerDraft(Base):
    """Capability binding, committed atomically with the existing content draft."""

    __tablename__ = "ad_customer_drafts"
    draft_id: Mapped[str] = mapped_column(ForeignKey("ad_content_drafts.id"), primary_key=True)
    grant_id: Mapped[int] = mapped_column()
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    customer_id: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            ["grant_id", "campaign_id", "customer_id"],
            [
                "ad_customer_grants.id",
                "ad_customer_grants.campaign_id",
                "ad_customer_grants.customer_id",
            ],
            name="fk_ad_draft_customer",
        ),
    )


class AdCustomerLink(Base):
    __tablename__ = "ad_customer_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    grant_id: Mapped[int] = mapped_column(ForeignKey("ad_customer_grants.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE")
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    revoked_by: Mapped[int | None] = mapped_column(BigInteger)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("length(token_hash) = 64 AND created_by > 0", name="ck_ad_link_hash"),
        CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL) OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND revoked_by > 0)",
            name="ck_ad_link_status",
        ),
        Index(
            "uq_ad_active_link",
            "grant_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class AdClientInvitation(Base):
    """Expiring bearer invitation. Store only its digest, never the raw deep-link token."""

    __tablename__ = "ad_client_invitations"
    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("ad_campaigns.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(8), default="ACTIVE")
    expected_grant: Mapped[str] = mapped_column(String(48))
    expected_customer_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_by: Mapped[int | None] = mapped_column(BigInteger)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grant_id: Mapped[int | None] = mapped_column(ForeignKey("ad_customer_grants.id"))
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','USED','REVOKED')", name="ck_ad_invite_status"),
        CheckConstraint("length(token_hash) = 64 AND created_by > 0", name="ck_ad_invite_hash"),
        CheckConstraint("expires_at > created_at", name="ck_ad_invite_expiry"),
        CheckConstraint(
            "expected_customer_id IS NULL OR expected_customer_id > 0", name="ck_ad_invite_customer"
        ),
        CheckConstraint(
            "(status = 'USED' AND consumed_by IS NOT NULL AND consumed_by > 0 AND consumed_at IS NOT NULL AND grant_id IS NOT NULL) "
            "OR (status <> 'USED' AND consumed_by IS NULL AND consumed_at IS NULL AND grant_id IS NULL)",
            name="ck_ad_invite_consumption",
        ),
        Index(
            "uq_ad_active_invite",
            "campaign_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class MerchantEntry(Base):
    """One directory identity per existing user; no reputation balance is stored."""

    __tablename__ = "merchant_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    name: Mapped[str | None] = mapped_column(String(64))
    name_key: Mapped[str | None] = mapped_column(String(128), index=True)
    city_code: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(600))
    contact: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(12), default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(String(600))
    owner: Mapped[User] = relationship()
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING','APPROVED','REJECTED','SUSPENDED','REMOVED')",
            name="ck_merchant_status",
        ),
        CheckConstraint("version > 0", name="ck_merchant_version"),
        CheckConstraint(
            "city_code IS NULL OR city_code IN ('vilnius','kaunas','klaipeda','siauliai','panevezys','pajurys')",
            name="ck_merchant_city",
        ),
        CheckConstraint(
            "status <> 'APPROVED' OR (name IS NOT NULL AND name_key IS NOT NULL AND city_code IS NOT NULL AND description IS NOT NULL AND approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="ck_merchant_published",
        ),
        Index("ix_merchant_city_status", "city_code", "status", "id"),
    )


class MerchantProposal(Base):
    """Historical applications retained for data compatibility; no active workflow."""

    __tablename__ = "merchant_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("merchant_entries.id"), index=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    name: Mapped[str | None] = mapped_column(String(64))
    city_code: Mapped[str | None] = mapped_column(String(16))
    description: Mapped[str | None] = mapped_column(String(600))
    base_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    request_key: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    moderated_by: Mapped[int | None] = mapped_column(BigInteger)
    moderated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(600))
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING','APPROVED','REJECTED','CANCELLED')",
            name="ck_merchant_proposal_status",
        ),
        CheckConstraint("kind IN ('CREATE','EDIT','REMOVE')", name="ck_merchant_proposal_kind"),
        CheckConstraint("base_version > 0 AND actor_id > 0", name="ck_merchant_proposal_identity"),
        Index(
            "uq_merchant_open_proposal",
            "entry_id",
            unique=True,
            sqlite_where=text("status IN ('DRAFT','PENDING')"),
            postgresql_where=text("status IN ('DRAFT','PENDING')"),
        ),
        Index("ix_merchant_proposal_queue", "status", "created_at", "id"),
        Index("ix_merchant_proposal_actor_time", "actor_id", "created_at"),
    )


class MerchantSearchGram(Base):
    """Indexed two-character postings allow substring name search without pg_trgm."""

    __tablename__ = "merchant_search_grams"
    entry_id: Mapped[int] = mapped_column(ForeignKey("merchant_entries.id"), primary_key=True)
    token: Mapped[str] = mapped_column(String(2), primary_key=True)
    __table_args__ = (Index("ix_merchant_gram_lookup", "token", "entry_id"),)


class MerchantSettings(Base):
    __tablename__ = "merchant_settings"
    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Historical schema compatibility only; applications have no active route or setting.
    registrations: Mapped[bool] = mapped_column(default=True)
    publish_authors: Mapped[bool] = mapped_column(default=False)
    copy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (CheckConstraint("id = 1 AND version > 0", name="ck_merchant_settings"),)

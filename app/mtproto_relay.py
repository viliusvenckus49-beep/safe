"""Optional staff transport, sharing SAFECheck's numeric-ID ban outbox and identity service."""

import asyncio
import hashlib
import json
import os
import stat
import uuid
from datetime import UTC, timedelta
from pathlib import Path
from time import monotonic
from typing import Any

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.methods import BanChatMember
from sqlalchemy import BigInteger, cast, select, update

from app.config import Settings
from app.models import AuditEvent, BanAction, ManagedGroup, ScamRecord, User, now
from app.repositories import Repository
from app.scam_management import ScamManagement
from app.services import DomainError, Service

_relay: "GroupHelpRelay | None" = None


def active_relay() -> "GroupHelpRelay | None":
    return _relay


def set_relay(value: "GroupHelpRelay | None") -> None:
    global _relay
    _relay = value


def private_configuration(directory: Path) -> dict:
    if (
        directory.is_symlink()
        or directory.stat().st_mode & 0o077
        or directory.stat().st_uid != os.getuid()
    ):
        raise ValueError("MTProto state directory must be private")
    for name in ("api.json", "account.session"):
        path = directory / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("MTProto files must be regular, private and owned by the runtime")
    fd = os.open(directory / "api.json", os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or set(value) != {"api_id", "api_hash"}:
        raise ValueError("Invalid MTProto configuration")
    if type(value["api_id"]) is not int or not 0 < value["api_id"] <= 2147483647:
        raise ValueError("Invalid api_id")
    if not isinstance(value["api_hash"], str) or len(value["api_hash"]) != 32:
        raise ValueError("Invalid api_hash")
    if any(c not in "0123456789abcdefABCDEF" for c in value["api_hash"]):
        raise ValueError("Invalid api_hash")
    return value


def due(event: AuditEvent | None) -> bool:
    return event is None or event.created_at.replace(tzinfo=UTC) < now() - timedelta(seconds=60)


class GroupHelpRelay:
    def __init__(self, settings: Settings, sessions: Any, client: Any, staff: Any):
        self.settings, self.sessions, self.client, self.staff = settings, sessions, client, staff
        # Both polling handlers and the background worker share this command gate.
        self.commands = asyncio.Lock()
        self.identity = asyncio.Lock()
        self.flood_until = now()
        self.lookups: dict[str, tuple[float, Any]] = {}

    async def lookup_username(self, username: str, *, refresh: bool = False) -> Any:
        """Share bounded caching; explicit checks refresh it while respecting flood waits."""
        async with self.identity:
            cached = self.lookups.get(username)
            if not refresh and cached is not None and cached[0] > monotonic():
                return cached[1]
            if self.flood_until > now():
                return None
            user = None
            try:
                user = await self.telegram_user(username)
            except Exception as error:
                await self._note_flood(error)
                structlog.get_logger().info(
                    "mtproto_username_lookup_failed",
                    username=username,
                    exception_type=type(error).__name__,
                )
            if len(self.lookups) >= 128:
                self.lookups.pop(next(iter(self.lookups)))
            self.lookups[username] = (monotonic() + (30 if user is not None else 15), user)
            return user

    @classmethod
    async def connect(cls, settings: Settings, sessions: Any) -> "GroupHelpRelay":
        from telethon import TelegramClient

        directory = Path(settings.group_help_state_dir)
        config = private_configuration(directory)
        os.umask(0o077)
        client = TelegramClient(
            str(directory / "account.session"),
            config["api_id"],
            config["api_hash"],
            flood_sleep_threshold=0,
            request_retries=1,
            connection_retries=2,
            retry_delay=1,
            timeout=5,
        )
        try:
            await asyncio.wait_for(client.connect(), 15)
            if not await client.is_user_authorized():
                raise ValueError("MTProto session is not authenticated")
            me = await client.get_me()
            staff = await client.get_entity(settings.group_help_staff_id)
            bot = await client.get_entity("ghStaffBot")
            if me is None or me.bot or getattr(staff, "left", False):
                raise ValueError("Staff user membership is required")
            if not bot.bot or bot.id != settings.group_help_bot_id:
                raise ValueError("Staff bot identity does not match its pinned ID")
            if not any(user.id == bot.id for user in await client.get_participants(staff)):
                raise ValueError("Staff bot is missing")
            relay = cls(settings, sessions, client, staff)
            if not await relay.scope_active():
                raise ValueError("Staff scope includes a withdrawn or unapproved group")
            await relay.activate(me.id)
            structlog.get_logger().info(
                "group_help_connected",
                account_id=me.id,
                staff_id=settings.group_help_staff_id,
                staff_bot_id=bot.id,
            )
            return relay
        except BaseException:
            await client.disconnect()
            raise

    async def close(self) -> None:
        await self.client.disconnect()

    async def scope_active(self, session: Any = None) -> bool:
        if session is None:
            async with self.sessions() as owned:
                return await self.scope_active(owned)
        groups = list(
            (
                await session.scalars(
                    select(ManagedGroup.chat_id).where(
                        ManagedGroup.chat_id.in_(self.settings.group_help_scope),
                        ManagedGroup.approved.is_(True),
                        ManagedGroup.enabled.is_(True),
                    )
                )
            ).all()
        )
        return len(groups) == len(self.settings.group_help_scope)

    async def activate(self, account_id: int) -> None:
        """Requeue previous transport failures once; preserve live leases and flood waits."""
        async with self.sessions() as session:
            core = Service(self.settings, session)
            await core.repo.lock_identity_metadata()
            owner = self.settings.group_owner
            if owner is None:
                raise ValueError("An authorized group owner is required")
            await core.require_admin(owner)
            if not await self.scope_active(session):
                raise ValueError("Staff scope was withdrawn")
            scope = ",".join(str(x) for x in sorted(self.settings.group_help_scope))
            seen = await session.scalar(
                select(AuditEvent.id)
                .where(
                    AuditEvent.action == "group_help_activated",
                    cast(AuditEvent.details["account_id"].as_string(), BigInteger) == account_id,
                    cast(AuditEvent.details["staff_id"].as_string(), BigInteger)
                    == self.settings.group_help_staff_id,
                    AuditEvent.details["scope"].as_string() == scope,
                )
                .limit(1)
            )
            last_flood = await session.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "mtproto_flood_wait",
                )
                .order_by(AuditEvent.id.desc())
                .limit(1)
            )
            if last_flood is not None:
                from datetime import datetime

                self.flood_until = max(
                    self.flood_until, datetime.fromisoformat(last_flood.details["until"])
                )
            if seen is None:
                active = (
                    select(ScamRecord.id)
                    .join(User)
                    .where(
                        ScamRecord.status == "ACTIVE",
                        User.telegram_id == BanAction.telegram_id,
                    )
                    .correlate(BanAction)
                )
                await session.execute(
                    update(BanAction)
                    .where(
                        BanAction.chat_id.in_(self.settings.group_help_scope),
                        BanAction.scam_record_id.in_(active),
                        BanAction.status == "FAILED",
                        (BanAction.result_type != "TelegramRetryAfter")
                        | BanAction.result_type.is_(None)
                        | (BanAction.next_attempt_at <= now()),
                    )
                    .values(status="PENDING", attempts=0, next_attempt_at=now(), completed_at=None)
                )
                core._audit(
                    owner,
                    "group_help_activated",
                    None,
                    account_id=account_id,
                    staff_id=self.settings.group_help_staff_id,
                    scope=scope,
                )
            await session.commit()

    async def _latest(self, session: Any, action: str, record_id: int) -> AuditEvent | None:
        return await session.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.action == action,
                AuditEvent.details["record_id"].as_integer() == record_id,
            )
            .order_by(AuditEvent.id.desc())
            .limit(1)
        )

    async def telegram_user(self, username: str) -> Any:
        from telethon.tl.functions.contacts import ResolveUsernameRequest

        # ResolveUsernameRequest bypasses the account's cached username association.
        result = await asyncio.wait_for(self.client(ResolveUsernameRequest(username)), 5)
        peer_id = getattr(result.peer, "user_id", None)
        user = next((u for u in result.users if u.id == peer_id), None)
        if (
            user is None
            or getattr(user, "deleted", False)
            or (user.username or "").casefold() != username.casefold()
        ):
            raise ValueError("Telegram did not return the requested public user")
        return user

    async def target_mention(self, user_id: int, username: str | None) -> list[Any]:
        """Expose an actual Telegram user entity in staff, never a fabricated numeric peer."""
        from telethon import utils
        from telethon.tl.types import InputMessageEntityMentionName

        try:
            try:
                peer = await self.client.get_input_entity(user_id)
                if getattr(peer, "user_id", None) != user_id:
                    raise ValueError("Not a cached user peer")
            except ValueError:
                if not username:
                    return []
                user = await self.telegram_user(username)
                if user.id != user_id:
                    return []  # A recycled username cannot redirect a numeric SCAM judgment.
                peer = await self.client.get_input_entity(user)
            return [
                InputMessageEntityMentionName(
                    offset=5,
                    length=len(str(user_id)),
                    user_id=utils.get_input_user(peer),
                )
            ]
        except Exception as error:
            await self._note_flood(error)
            structlog.get_logger().info(
                "group_help_peer_not_available",
                user_id=user_id,
                exception_type=type(error).__name__,
            )
            return []

    async def resolve_record(self, record_id: int) -> bool:
        """Fresh Telegram resolution; existing numeric associations are never overwritten."""
        async with self.identity:
            if self.flood_until > now():
                return False
            async with self.sessions() as session:
                core = Service(self.settings, session)
                await core.repo.lock_identity_metadata()
                record = await core.repo.scam_by_id(record_id)
                if (
                    record is None
                    or record.status != "ACTIVE"
                    or record.target.telegram_id is not None
                    or not record.target.username
                ):
                    await session.rollback()
                    return False
                last = await self._latest(session, "mtproto_identity_attempt", record_id)
                refresh = await self._latest(session, "scam_manual_retry", record_id)
                reset = refresh is not None and (last is None or refresh.id > last.id)
                if not reset and (
                    not due(last) or (last is not None and last.details.get("attempt", 0) >= 8)
                ):
                    await session.rollback()
                    return False
                actor, username = (
                    self.settings.group_owner or record.moderator_id,
                    record.target.username,
                )
                try:
                    await core.require_admin(actor)
                except DomainError:
                    await session.rollback()
                    return False
                attempt = 1 if last is None or reset else int(last.details.get("attempt", 0)) + 1
                core._audit(
                    actor,
                    "mtproto_identity_attempt",
                    record.target_id,
                    record_id=record_id,
                    attempt=attempt,
                )
                await session.commit()
            try:
                user = await self.telegram_user(username)
                async with self.sessions() as session:
                    core = Service(self.settings, session)
                    await ScamManagement(core).supplement(
                        actor,
                        record_id,
                        "id",
                        str(user.id),
                        uuid.uuid4().hex,
                        verified_username=username,
                    )
                    name = " ".join(x for x in (user.first_name, user.last_name) if x)
                    await core.observe(user.id, user.username, name or "User")
                structlog.get_logger().info(
                    "mtproto_scam_identity_linked",
                    scam_record_id=record_id,
                    user_id=user.id,
                )
                return True
            except Exception as error:
                await self._note_flood(error)
                structlog.get_logger().warning(
                    "mtproto_scam_identity_failed",
                    scam_record_id=record_id,
                    exception_type=type(error).__name__,
                )
                return False

    async def resolve_pending(self) -> int:
        async with self.sessions() as session:
            attempts = (
                select(AuditEvent)
                .where(
                    AuditEvent.action == "mtproto_identity_attempt",
                    AuditEvent.details["record_id"].as_integer() == ScamRecord.id,
                )
                .correlate(ScamRecord)
            )
            latest_attempt = (
                attempts.with_only_columns(AuditEvent.details["attempt"].as_integer())
                .order_by(AuditEvent.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            latest_id = (
                attempts.with_only_columns(AuditEvent.id)
                .order_by(AuditEvent.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            refresh_id = (
                select(AuditEvent.id)
                .where(
                    AuditEvent.action == "scam_manual_retry",
                    AuditEvent.details["record_id"].as_integer() == ScamRecord.id,
                )
                .correlate(ScamRecord)
                .order_by(AuditEvent.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            record_id = await session.scalar(
                select(ScamRecord.id)
                .join(User)
                .where(
                    ScamRecord.status == "ACTIVE",
                    User.telegram_id.is_(None),
                    User.username.is_not(None),
                    ~attempts.where(
                        AuditEvent.created_at >= now() - timedelta(seconds=60)
                    ).exists(),
                    latest_attempt.is_(None) | (latest_attempt < 8) | (refresh_id > latest_id),
                )
                .order_by(ScamRecord.id)
                .limit(1)
            )
        return int(record_id is not None and await self.resolve_record(record_id))

    async def _note_flood(self, error: Exception) -> None:
        if type(error).__name__ in {"FloodWaitError", "FloodPremiumWaitError"}:
            self.flood_until = max(
                self.flood_until,
                now()
                + timedelta(
                    seconds=max(1, int(getattr(error, "seconds", 60))),
                ),
            )
            async with self.sessions() as session:
                session.add(
                    AuditEvent(
                        actor_id=self.settings.group_owner or 0,
                        action="mtproto_flood_wait",
                        details={"until": self.flood_until.isoformat()},
                    )
                )
                await session.commit()

    async def dispatch(self, service: Any, job: Any) -> bool:
        """At-least-once ban submission, serialized and durably throttled per SCAM record."""
        async with self.commands:
            if self.flood_until > now():
                raise TelegramRetryAfter(
                    method=BanChatMember(chat_id=job.chat_id, user_id=job.telegram_id),
                    message="MTProto flood wait",
                    retry_after=max(1, int((self.flood_until - now()).total_seconds())),
                )
            if not await self.scope_active() or not await service.ban_eligible(job.id):
                return False
            async with self.sessions() as session:
                repo = Repository(session)
                await repo.lock_identity_metadata()
                if not await self.scope_active(session):
                    await session.rollback()
                    return False
                record = await repo.scam_by_id(job.scam_record_id)
                if (
                    record is None
                    or record.status != "ACTIVE"
                    or record.target.telegram_id != job.telegram_id
                ):
                    await session.rollback()
                    return False
                username = record.target.username
                last = await self._latest(session, "group_help_dispatch", record.id)
                if not due(last):
                    await session.rollback()
                    return True  # Only says a recent request exists; not a verified ban.
                refresh = await self._latest(session, "scam_manual_retry", record.id)
                reset = refresh is not None and (last is None or refresh.id > last.id)
                attempt = 1 if last is None or reset else int(last.details.get("attempt", 0)) + 1
                if attempt > 8:
                    await session.rollback()
                    return False
                event = AuditEvent(
                    actor_id=record.moderator_id,
                    action="group_help_dispatch",
                    target_id=record.target_id,
                    details={
                        "record_id": record.id,
                        "user_id": job.telegram_id,
                        "staff_id": self.settings.group_help_staff_id,
                        "attempt": attempt,
                    },
                )
                session.add(event)
                await session.commit()
                event_id = event.id
            try:
                from telethon.tl.functions.messages import SendMessageRequest

                random_id = int.from_bytes(
                    hashlib.sha256(
                        f"safecheck-staff:{self.settings.group_help_bot_id}:{event_id}".encode()
                    ).digest()[:8],
                    "big",
                ) & ((1 << 63) - 1)
                entities = await self.target_mention(job.telegram_id, username)
                if self.flood_until > now():
                    raise TimeoutError("Peer lookup is rate-limited")
                # Group Help's staff parser accepts plain commands, not /ban@ghStaffBot.
                await self.client(
                    SendMessageRequest(
                        peer=await self.client.get_input_entity(self.staff),
                        message=f"/ban {job.telegram_id}",
                        random_id=random_id,
                        no_webpage=True,
                        entities=entities,
                    )
                )
                structlog.get_logger().info(
                    "group_help_command_submitted",
                    operation="staff_ban",
                    user_id=job.telegram_id,
                    staff_id=self.settings.group_help_staff_id,
                    scam_record_id=job.scam_record_id,
                )
                return True
            except Exception as error:
                await self._note_flood(error)
                structlog.get_logger().warning(
                    "group_help_command_failed",
                    operation="staff_ban",
                    user_id=job.telegram_id,
                    staff_id=self.settings.group_help_staff_id,
                    exception_type=type(error).__name__,
                )
                if self.flood_until > now():
                    raise TelegramRetryAfter(
                        method=BanChatMember(chat_id=job.chat_id, user_id=job.telegram_id),
                        message="MTProto flood wait",
                        retry_after=max(1, int((self.flood_until - now()).total_seconds())),
                    ) from None
                return False

    async def attempt_ban(self, bot: Any, service: Any, job: Any, timeout: float):
        from app.bot.group_runtime import _attempt_ban

        if job.chat_id not in self.settings.group_help_scope:
            return await _attempt_ban(bot, job, timeout)
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(chat_id=job.chat_id, user_id=job.telegram_id),
                timeout=min(2, timeout / 3),
            )
            if member.status == "kicked":
                return True, "ALREADY_BANNED"
        except TelegramRetryAfter:
            raise
        except (TelegramAPIError, TimeoutError, OSError):
            pass
        if await self.dispatch(service, job):
            for _ in range(3):
                await asyncio.sleep(0.5)
                try:
                    member = await asyncio.wait_for(
                        bot.get_chat_member(chat_id=job.chat_id, user_id=job.telegram_id),
                        timeout=min(1, timeout / 3),
                    )
                    if member.status == "kicked":
                        return True, "BANNED"
                except TelegramRetryAfter:
                    raise
                except (TelegramAPIError, TimeoutError, OSError):
                    pass
        # A staff command never counts as success; keep the existing numeric API fallback.
        if not await service.ban_eligible(job.id):
            return False, "OBSOLETE"
        return await _attempt_ban(bot, job, timeout)

"""Local moderation contracts use real sessions and a stateful Telegram API double."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import BanChatMember
from aiogram.types import ChatPermissions
from sqlalchemy import func, select, update
from test_integration_contracts import postgres_contract as postgres_contract

from app.bot.local_moderation import MUTE_PERMISSIONS, LocalModerationExecutor
from app.errors import DomainError
from app.models import (
    AuditEvent,
    BanAction,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    ManagedGroup,
    ModerationAction,
    ScamRecord,
    now,
)
from app.moderation_services import GroupModerationService
from app.services import Service

pytestmark = pytest.mark.asyncio
CHAT = -1004430959898
OTHER = -1003976343267


class NativeBot:
    id = 123456

    def __init__(self):
        self.members = {}
        self.calls = []
        self.error = None
        self.api_false = False
        self.defaults = ChatPermissions(can_send_messages=True, can_send_photos=False)
        self.admin(CHAT, 42)
        self.admin(OTHER, 42)
        for chat in (CHAT, OTHER):
            self.members[chat, self.id] = self.peer(
                self.id,
                "administrator",
                is_bot=True,
                can_restrict_members=True,
                can_change_info=True,
            )

    @staticmethod
    def peer(user_id, status="member", *, is_bot=False, **kwargs):
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id, is_bot=is_bot), status=status, **kwargs
        )

    def admin(self, chat, user_id, **rights):
        self.members[chat, user_id] = self.peer(
            user_id,
            "administrator",
            can_restrict_members=rights.get("restrict", True),
            can_change_info=rights.get("configure", True),
        )

    async def get_chat_member(self, chat_id, user_id):
        return self.members.get((chat_id, user_id), self.peer(user_id))

    async def get_chat(self, chat_id):
        return SimpleNamespace(id=chat_id, type="supergroup", permissions=self.defaults)

    async def ban_chat_member(self, **kwargs):
        self.calls.append(("ban", kwargs))
        if self.error:
            raise self.error
        if self.api_false:
            return False
        self.members[kwargs["chat_id"], kwargs["user_id"]] = self.peer(
            kwargs["user_id"], "kicked", until_date=kwargs.get("until_date")
        )
        return True

    async def unban_chat_member(self, **kwargs):
        self.calls.append(("unban", kwargs))
        if self.api_false:
            return False
        self.members[kwargs["chat_id"], kwargs["user_id"]] = self.peer(kwargs["user_id"], "left")
        return True

    async def restrict_chat_member(self, **kwargs):
        self.calls.append(("restrict", kwargs))
        if self.error:
            raise self.error
        if self.api_false:
            return False
        self.members[kwargs["chat_id"], kwargs["user_id"]] = self.peer(
            kwargs["user_id"],
            "restricted",
            until_date=kwargs.get("until_date"),
            **kwargs["permissions"].model_dump(exclude_none=True),
        )
        return True


async def setup_groups(sessions, bot, *, enabled=True):
    async with sessions() as session:
        session.add_all(
            [
                ManagedGroup(chat_id=CHAT, title="One", approved=True),
                ManagedGroup(chat_id=OTHER, title="Two", approved=True),
            ]
        )
        await session.commit()
        if enabled:
            await GroupModerationService(session, bot).configure(
                CHAT, 42, enabled=True, bans_enabled=True, mutes_enabled=True
            )


async def submit(
    sessions, bot, *, target=51, chat=CHAT, actor=42, key="request-0001", kind="BAN", until=None
):
    until = until or (now() + timedelta(hours=1)).replace(microsecond=0)
    async with sessions() as session:
        return await GroupModerationService(session, bot).submit(
            chat, actor, target, kind, until_date=until, reason="Local group rule", key=key
        )


@pytest.mark.parametrize("actor", [900, 1])
async def test_global_admin_or_regular_user_has_no_local_rights(database, settings, actor):
    bot = NativeBot()
    await setup_groups(database, bot, enabled=False)
    assert actor in settings.admins or actor == 1
    async with database() as session:
        with pytest.raises(DomainError, match="group_moderation_forbidden"):
            await GroupModerationService(session, bot).configure(CHAT, actor, enabled=True)
        assert await session.scalar(select(func.count()).select_from(GroupModerationSettings)) == 0
    assert bot.calls == []


async def test_local_admin_without_global_grant_and_group_isolation(database, settings):
    bot = NativeBot()
    await setup_groups(database, bot)
    assert 42 not in settings.admins
    async with database() as session:
        policy = await session.get(GroupModerationSettings, CHAT)
        assert policy.enabled and not policy.allow_permanent_bans
        assert await session.get(GroupModerationSettings, OTHER) is None
    with pytest.raises(DomainError, match="group_moderation_disabled"):
        await submit(database, bot, chat=OTHER)
    bot.members[OTHER, 42] = bot.peer(42)
    async with database() as session:
        with pytest.raises(DomainError, match="group_moderation_forbidden"):
            await GroupModerationService(session, bot).configure(OTHER, 42, enabled=True)


@pytest.mark.parametrize("restrict,configure", [(False, True), (True, False)])
async def test_real_telegram_capabilities_and_creator(database, restrict, configure):
    bot = NativeBot()
    await setup_groups(database, bot, enabled=False)
    bot.admin(CHAT, 42, restrict=restrict, configure=configure)
    async with database() as session:
        with pytest.raises(DomainError, match="forbidden"):
            await GroupModerationService(session, bot).configure(
                CHAT, 42, enabled=True, bans_enabled=True, mutes_enabled=True
            )
        bot.members[CHAT, 42] = bot.peer(42, "creator")
        await GroupModerationService(session, bot).configure(
            CHAT, 42, enabled=True, bans_enabled=True, mutes_enabled=True
        )


@pytest.mark.parametrize("target", [True, 0, -1, 2**63, "51"])
async def test_invalid_numeric_targets_are_rejected_before_telegram(database, target):
    bot = NativeBot()
    await setup_groups(database, bot)
    with pytest.raises(DomainError, match="invalid_id"):
        await submit(database, bot, target=target)
    assert bot.calls == []


async def test_identity_mismatch_and_protected_targets(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    bot.members[CHAT, 42] = bot.peer(43, "creator")
    with pytest.raises(DomainError, match="identity_mismatch"):
        await submit(database, bot)
    bot.admin(CHAT, 42)
    for target in [42, bot.id]:
        with pytest.raises(DomainError, match="protected_target"):
            await submit(database, bot, target=target)


async def test_bot_rights_required_and_disable_allowed_when_lost(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    bot.members[CHAT, bot.id].can_restrict_members = False
    with pytest.raises(DomainError, match="bot_rights"):
        await submit(database, bot)
    async with database() as session:
        policy = await GroupModerationService(session, bot).configure(CHAT, 42, enabled=False)
        assert not policy.enabled


async def test_idempotent_submission_and_request_payload_binding(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    until = (now() + timedelta(hours=1)).replace(microsecond=0)
    a = await submit(database, bot, until=until)
    b = await submit(database, bot, until=until)
    assert a.id == b.id
    with pytest.raises(DomainError, match="request_conflict"):
        await submit(database, bot, target=52, until=until)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 1
        assert await session.scalar(select(func.count()).select_from(GroupSanctionJob)) == 2
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(ModerationAction)) == 0
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0


async def test_pending_jobs_survive_fresh_executor_and_never_repeat(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    assert await LocalModerationExecutor(database, bot).process() == 1
    assert await LocalModerationExecutor(database, bot).process() == 0
    async with database() as session:
        row = await session.get(GroupSanction, sanction.id)
        assert row.status == "ACTIVE" and row.owned
        audits = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action == "group_moderation_attempt")
            )
        )
        assert len(audits) == 1 and audits[0].details["success"] is True
    assert len(bot.calls) == 1 and bot.calls[0][0] == "ban"


@pytest.mark.parametrize("change", ["disable", "rights", "target_admin", "remove", "policy"])
async def test_worker_revalidates_before_mutating_telegram(database, change):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    if change == "rights":
        bot.members[CHAT, 42] = bot.peer(42)
    elif change == "target_admin":
        bot.admin(CHAT, 51)
    else:
        async with database() as session:
            if change == "remove":
                session.add(
                    AuditEvent(actor_id=900, action="group_removed", details={"chat_id": CHAT})
                )
                await session.commit()
            else:
                await GroupModerationService(session, bot).configure(
                    CHAT,
                    42,
                    enabled=change != "disable",
                    max_duration_seconds=7200 if change == "policy" else 2592000,
                )
    await LocalModerationExecutor(database, bot).process()
    assert bot.calls == []
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "CANCELLED"


async def test_lease_fences_duplicate_workers_and_expiry_never_replays(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    claim = await worker.claim()
    assert claim is not None and await worker.claim() is None
    await worker.execute(claim[0], "wrong-token")
    assert bot.calls == []
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.id == claim[0])
            .values(lease_until=now() - timedelta(seconds=1))
        )
        await session.commit()
    assert await LocalModerationExecutor(database, bot).process() == 1
    await worker.execute(*claim)
    assert bot.calls == []
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "UNCERTAIN"
        assert (await session.get(GroupSanctionJob, claim[0])).result == "LEASE_EXPIRED"


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError(),
        TelegramRetryAfter(
            method=BanChatMember(chat_id=CHAT, user_id=51), message="flood", retry_after=120
        ),
    ],
)
async def test_timeout_is_uncertain_but_flood_wait_is_scheduled(database, error):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    bot.error = error
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        job = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "APPLY"
            )
        )
        assert job.status == ("PENDING" if isinstance(error, TelegramRetryAfter) else "UNCERTAIN")
        if isinstance(error, TelegramRetryAfter):
            assert (job.available_at.replace(tzinfo=UTC) - now()).total_seconds() > 110
    await LocalModerationExecutor(database, bot).process()
    assert len(bot.calls) == 1


async def test_false_or_rejected_api_is_not_success(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    bot.api_false = True
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "FAILED"
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "group_moderation_attempt")
        )
        assert audit.details["success"] is False and audit.details["result"] == "API_FALSE"


async def test_revoke_removes_only_our_ban_and_is_idempotent(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    async with database() as session:
        service = GroupModerationService(session, bot)
        await service.revoke(CHAT, 42, sanction.id, key="revoke-0001")
        await service.revoke(CHAT, 42, sanction.id, key="revoke-0001")
    await worker.process()
    await worker.process()
    assert [c[0] for c in bot.calls] == ["ban", "unban"]
    assert bot.calls[1][1]["only_if_banned"] is True
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "REVOKED"


@pytest.mark.parametrize("external", ["preexisting", "changed", "scam"])
async def test_revocation_never_unbans_external_or_scam_restrictions(database, settings, external):
    bot = NativeBot()
    await setup_groups(database, bot)
    until = (now() + timedelta(hours=1)).replace(microsecond=0)
    if external == "preexisting":
        bot.members[CHAT, 51] = bot.peer(51, "kicked", until_date=until)
    sanction = await submit(database, bot, until=until)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    if external == "changed":
        bot.members[CHAT, 51].until_date = until + timedelta(hours=2)
    if external == "scam":
        async with database() as session:
            await Service(settings, session).add_scam(900, "51")
    async with database() as session:
        await GroupModerationService(session, bot).revoke(CHAT, 42, sanction.id, key="revoke-0001")
    await worker.process()
    assert not any(call[0] == "unban" for call in bot.calls)
    async with database() as session:
        if external == "scam":
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ScamRecord)
                    .where(ScamRecord.status == "ACTIVE")
                )
                == 1
            )


async def test_mute_release_uses_current_group_defaults_and_independent_permissions(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot, kind="MUTE")
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    assert bot.calls[0][1]["permissions"] == MUTE_PERMISSIONS
    async with database() as session:
        await GroupModerationService(session, bot).revoke(CHAT, 42, sanction.id, key="revoke-0001")
    bot.defaults = ChatPermissions(
        can_send_messages=True, can_send_photos=False, can_send_videos=False
    )
    await worker.process()
    assert bot.calls[-1][1]["permissions"] == bot.defaults
    assert bot.calls[-1][1]["use_independent_chat_permissions"] is True


async def concurrency_contract(sessions):
    bot = NativeBot()
    await setup_groups(sessions, bot)
    until = (now() + timedelta(hours=1)).replace(microsecond=0)
    results = await asyncio.gather(*(submit(sessions, bot, until=until) for _ in range(2)))
    assert results[0].id == results[1].id
    first, second = LocalModerationExecutor(sessions, bot), LocalModerationExecutor(sessions, bot)
    claims = await asyncio.gather(first.claim(), second.claim())
    assert sum(c is not None for c in claims) == 1
    claim = next(c for c in claims if c is not None)
    await asyncio.gather(first.execute(*claim), second.execute(*claim))
    assert len(bot.calls) == 1


async def test_sqlite_parallel_idempotence_and_claims(database):
    await concurrency_contract(database)


async def test_postgres_parallel_idempotence_and_claims(postgres_contract):
    sessions, _ = postgres_contract
    await concurrency_contract(sessions)


async def test_individual_modules_default_off_and_group_scoped(database):
    bot = NativeBot()
    await setup_groups(database, bot, enabled=False)
    async with database() as session:
        policy = await GroupModerationService(session, bot).configure(CHAT, 42, enabled=True)
        assert not policy.bans_enabled and not policy.mutes_enabled
    for kind in ("BAN", "MUTE"):
        with pytest.raises(DomainError, match="disabled"):
            await submit(database, bot, kind=kind)
    async with database() as session:
        await GroupModerationService(session, bot).configure(
            CHAT, 42, enabled=True, bans_enabled=True
        )
    await submit(database, bot)
    with pytest.raises(DomainError, match="disabled"):
        await submit(database, bot, target=52, kind="MUTE", key="request-0002")
    with pytest.raises(DomainError, match="disabled"):
        await submit(database, bot, chat=OTHER, target=52, key="request-0003")


@pytest.fixture
def moderation_clock(monkeypatch):
    # Whole-second deadlines match Telegram's wire precision. Do not measure DB /
    # authorization latency when testing the service's inclusive duration limits.
    clock = SimpleNamespace(current=datetime(2026, 10, 19, 12, tzinfo=UTC))
    monkeypatch.setattr("app.moderation_services.now", lambda: clock.current)
    return clock


async def check_expiry_boundary(sessions, clock, *, seconds, allowed, kind="BAN", advance=None):
    bot = NativeBot()
    await setup_groups(sessions, bot)
    until = clock.current + timedelta(seconds=seconds)
    if advance is not None:
        original = bot.get_chat_member
        checked_at = clock.current + advance

        async def delayed_member(chat_id, user_id):
            clock.current = checked_at
            return await original(chat_id, user_id)

        bot.get_chat_member = delayed_member
    if allowed:
        result = await submit(sessions, bot, kind=kind, until=until)
        assert result.status == "PENDING"
    else:
        with pytest.raises(DomainError, match="invalid_duration"):
            await submit(sessions, bot, kind=kind, until=until)
    async with sessions() as session:
        sanctions = list(await session.scalars(select(GroupSanction)))
        jobs = list(await session.scalars(select(GroupSanctionJob)))
        requested = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "group_moderation_requested")
        )
        assert len(sanctions) == int(allowed)
        assert len(jobs) == (2 if allowed else 0)
        assert requested == int(allowed)
        if allowed:
            deadline = until.replace(microsecond=0)
            assert sanctions[0].kind == kind
            assert sanctions[0].status == "PENDING"
            assert sanctions[0].until_date.replace(tzinfo=UTC) == deadline
            assert sorted(job.operation for job in jobs) == ["APPLY", "RELEASE"]
            release = next(job for job in jobs if job.operation == "RELEASE")
            assert release.available_at.replace(tzinfo=UTC) == deadline
    assert bot.calls == []


@pytest.mark.parametrize("seconds", [-1, 20, 2592001])
async def test_expiry_limits_cannot_accidentally_create_permanent_bans(
    database, moderation_clock, seconds
):
    await check_expiry_boundary(database, moderation_clock, seconds=seconds, allowed=False)


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize(
    "seconds,allowed",
    [
        (0, False),
        (59, False),
        (60, True),
        (61, True),
        (2591999, True),
        (2592000, True),
        (2592001, False),
    ],
)
async def test_expiry_inclusive_boundaries(database, moderation_clock, kind, seconds, allowed):
    await check_expiry_boundary(
        database, moderation_clock, seconds=seconds, allowed=allowed, kind=kind
    )


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize(
    "seconds,allowed", [(59, False), (60, True), (2592000, True), (2592001, False)]
)
async def test_postgres_expiry_boundaries(
    postgres_contract, moderation_clock, kind, seconds, allowed
):
    sessions, _ = postgres_contract
    await check_expiry_boundary(
        sessions, moderation_clock, seconds=seconds, allowed=allowed, kind=kind
    )


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
@pytest.mark.parametrize(
    "microsecond,seconds,elapsed_ms,allowed",
    [
        (900000, 2592001, 0, False),
        (900000, 2592001, 200, True),
        (900000, 60, 0, False),
        (900000, 61, 0, True),
        (0, 61, 2000, False),
    ],
)
async def test_expiry_uses_remaining_wire_duration_after_authorization(
    database, moderation_clock, kind, microsecond, seconds, elapsed_ms, allowed
):
    # Advance logical time, not wall time: the old CI failure is the second case.
    # A duration that falls below 60 s while checking permissions must be rejected.
    moderation_clock.current = moderation_clock.current.replace(microsecond=microsecond)
    await check_expiry_boundary(
        database,
        moderation_clock,
        seconds=seconds,
        allowed=allowed,
        kind=kind,
        advance=timedelta(milliseconds=elapsed_ms),
    )


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
async def test_omitted_duration_is_permanent_without_legacy_permission_flags(database, kind):
    bot = NativeBot()
    await setup_groups(database, bot)
    async with database() as session:
        await GroupModerationService(session, bot).submit(
            CHAT, 42, 51, kind, until_date=None, reason="Rule", key="permanent-01"
        )
    await LocalModerationExecutor(database, bot).process()
    assert bot.calls[0][1]["until_date"] is None


async def test_cross_chat_revoke_and_processing_revoke_are_denied(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    claim = await worker.claim()
    async with database() as session:
        with pytest.raises(DomainError, match="not_found"):
            await GroupModerationService(session, bot).revoke(
                OTHER, 42, sanction.id, key="revoke-other"
            )
        with pytest.raises(DomainError, match="busy"):
            await GroupModerationService(session, bot).revoke(
                CHAT, 42, sanction.id, key="revoke-here1"
            )
    await worker.execute(*claim)
    assert len(bot.calls) == 1


async def test_failed_release_preserves_active_restriction_and_ownership(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    async with database() as session:
        await GroupModerationService(session, bot).revoke(CHAT, 42, sanction.id, key="revoke-0001")
    bot.api_false = True
    await worker.process()
    async with database() as session:
        sanction = await session.get(GroupSanction, sanction.id)
        assert sanction.status == "ACTIVE" and sanction.owned
        job = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == sanction.id, GroupSanctionJob.operation == "RELEASE"
            )
        )
        assert job.status == "FAILED"


async def competing_requests(sessions):
    bot = NativeBot()
    await setup_groups(sessions, bot)
    results = await asyncio.gather(
        submit(sessions, bot, key="competing-01"),
        submit(sessions, bot, key="competing-02"),
        return_exceptions=True,
    )
    assert sum(isinstance(r, GroupSanction) for r in results) == 1
    assert sum(isinstance(r, DomainError) for r in results) == 1
    await LocalModerationExecutor(sessions, bot).process()
    assert len(bot.calls) == 1


async def test_sqlite_competing_restrictions_cannot_both_apply(database):
    await competing_requests(database)


async def test_postgres_competing_restrictions_cannot_both_apply(postgres_contract):
    sessions, _ = postgres_contract
    await competing_requests(sessions)


async def test_automatic_expiry_survives_disable_and_revoked_actor_rights(database, monkeypatch):
    from app.bot import local_moderation

    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    worker = LocalModerationExecutor(database, bot)
    await worker.process()
    async with database() as session:
        await GroupModerationService(session, bot).configure(CHAT, 42, enabled=False)
    expired = sanction.until_date + timedelta(seconds=1)
    monkeypatch.setattr(local_moderation, "now", lambda: expired)
    bot.members[CHAT, 42] = bot.peer(42)
    await LocalModerationExecutor(database, bot).process()
    assert [c[0] for c in bot.calls] == ["ban", "unban"]
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "EXPIRED"


@pytest.mark.parametrize("error_type", [TelegramBadRequest, TelegramForbiddenError])
async def test_native_api_rejections_are_audited_as_failures(database, error_type):
    bot = NativeBot()
    await setup_groups(database, bot)
    sanction = await submit(database, bot)
    bot.error = error_type(method=BanChatMember(chat_id=CHAT, user_id=51), message="Denied")
    await LocalModerationExecutor(database, bot).process()
    async with database() as session:
        assert (await session.get(GroupSanction, sanction.id)).status == "FAILED"
        audit = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "group_moderation_attempt")
        )
        assert audit.details["success"] is False
        assert audit.details["result"] == error_type.__name__


async def test_waiting_expiry_jobs_cannot_starve_ready_applications(database):
    bot = NativeBot()
    await setup_groups(database, bot)
    until = (now() + timedelta(hours=1)).replace(microsecond=0)
    for target in range(100, 121):
        await submit(database, bot, target=target, until=until, key=f"starvation-{target}")
    async with database() as session:
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.operation == "APPLY")
            .values(available_at=now() + timedelta(hours=2))
        )
        await session.execute(
            update(GroupSanctionJob)
            .where(GroupSanctionJob.operation == "RELEASE")
            .values(available_at=now() - timedelta(seconds=1))
        )
        await session.commit()
    await submit(database, bot, target=200, until=until, key="starvation-ready")
    assert await LocalModerationExecutor(database, bot).process() == 1
    assert len(bot.calls) == 1 and bot.calls[0][1]["user_id"] == 200

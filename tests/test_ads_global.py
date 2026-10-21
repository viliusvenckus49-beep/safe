"""GLOBAL ADS policy, authority and independent SQL delivery contracts; no Telegram network."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from test_ads_publication import INSTANT, configured, snapshot
from test_ads_publication import clock as clock
from test_integration_contracts import postgres_contract as postgres_contract

import app.ads_global as global_module
from app.ads_global import GlobalAdsService
from app.ads_publication import PublicationService
from app.ads_services import AdsService
from app.bot.ads_publisher import AdsPublisher
from app.errors import DomainError
from app.models import (
    AdGlobalOperation,
    Administrator,
    AdPublicationAttempt,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
)


@pytest.fixture(autouse=True)
def global_clock(clock, monkeypatch):
    monkeypatch.setattr(global_module, "now", lambda: clock[0])


async def setup(sessions, settings, clock):
    cid, sid, bot, _, _ = await configured(sessions, settings, clock)
    async with sessions() as session:
        session.add_all(
            [
                ManagedGroup(chat_id=chat, title=f"Group {chat}", approved=True, enabled=True)
                for chat in [-200, -300]
            ]
        )
        await session.commit()
        service = GlobalAdsService(settings, session)
        policy = await service.policy(900, cid)
        policy = await service.defaults(900, cid, policy.version, "interval_seconds", 1800)
        await service.defaults(900, cid, policy.version, "start_at", INSTANT)
    return cid, sid, bot


async def finish(sessions, settings, opid, bot):
    for _ in range(100):
        async with sessions() as session:
            service = GlobalAdsService(settings, session)
            await service.run(opid, bot)
            op = await session.get(AdGlobalOperation, opid, populate_existing=True)
            if op.status == "DONE":
                return
    raise AssertionError("Operation did not complete")


async def assign(
    sessions, settings, cid, bot, mode="SELECTED", chats=(-100, -200, -300), request="assign"
):
    async with sessions() as session:
        service = GlobalAdsService(settings, session)
        op = await service.selection(900, cid, mode, request)
        if mode in {"SINGLE", "SELECTED"}:
            for chat in chats:
                op = await service.toggle(900, op.id, op.version, chat, bot)
        op = await service.ready_selection(900, op.id, op.version, bot)
        await service.confirm(900, op.id, op.version)
        oid = op.id
    await finish(sessions, settings, oid, bot)
    return oid


async def action(sessions, settings, cid, bot, name, *, sid=None, request=None):
    async with sessions() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, name, request or "action-" + name, bot, sid=sid)
        await service.confirm(900, op.id, op.version)
        oid = op.id
    await finish(sessions, settings, oid, bot)
    return oid


async def rows(sessions, cid):
    async with sessions() as session:
        return list(
            await session.scalars(
                select(AdSchedule).where(AdSchedule.campaign_id == cid).order_by(AdSchedule.id)
            )
        )


async def test_shared_campaign_independent_intervals_and_pause(database, settings, clock):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    schedules = await rows(database, cid)
    assert len(schedules) == 3 and [r.status for r in schedules] == ["ACTIVE", "DRAFT", "DRAFT"]
    async with database() as session:
        op = await GlobalAdsService(settings, session).prepare(
            900,
            cid,
            "CHANGE",
            "individual",
            bot,
            sid=schedules[1].id,
            expected_version=schedules[1].version,
            payload={"field": "interval_seconds", "value": 14400},
        )
        await GlobalAdsService(settings, session).confirm(900, op.id, op.version)
        oid = op.id
    await finish(database, settings, oid, bot)
    await action(database, settings, cid, bot, "START")
    schedules = await rows(database, cid)
    assert [r.interval_seconds for r in schedules] == [1800, 14400, 1800]
    assert all(r.status == "ACTIVE" for r in schedules)
    await action(database, settings, cid, bot, "PAUSE", sid=schedules[1].id, request="one-pause")
    assert [r.status for r in await rows(database, cid)] == ["ACTIVE", "PAUSED", "ACTIVE"]
    await action(database, settings, cid, bot, "START", sid=schedules[1].id, request="one-resume")
    await AdsPublisher(bot, settings, database).tick()
    async with database() as session:
        attempts = list(await session.scalars(select(AdPublicationAttempt)))
        assert len(attempts) == 3 and len({r.content_version_id for r in attempts}) == 1
        stats = await GlobalAdsService(settings, session).statistics(900, cid)
        assert stats["sent"] == 3 and stats["groups"] == 3


async def test_bulk_pause_resume_duplicate_confirm_and_removed_tombstone(database, settings, clock):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot, mode="EXISTING")
    await action(database, settings, cid, bot, "START")
    await AdsPublisher(bot, settings, database).tick()
    oid = await action(database, settings, cid, bot, "PAUSE")
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.operation(900, oid)
        await service.confirm(900, oid, op.version)
    assert all(r.status == "PAUSED" for r in await rows(database, cid))
    await action(database, settings, cid, bot, "START", request="resume")
    second = (await rows(database, cid))[1]
    await action(database, settings, cid, bot, "REMOVE", sid=second.id)
    clock[0] += timedelta(seconds=60)
    async with database() as session:
        await GlobalAdsService(settings, session).reconcile(bot)
        assert await session.scalar(select(func.count()).select_from(AdPublicationAttempt)) == 3
        removed = await session.get(AdSchedule, second.id)
        assert removed.excluded and removed.status == "STOPPED"
    assert len(await rows(database, cid)) == 3


@pytest.mark.parametrize("mode", ["SINGLE", "SELECTED", "EXISTING"])
async def test_assignment_modes_and_future_groups_require_explicit_policy(
    database, settings, clock, mode
):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(
        database, settings, cid, bot, mode=mode, chats=(-200,) if mode == "SINGLE" else (-100, -200)
    )
    async with database() as session:
        session.add(ManagedGroup(chat_id=-400, title="Future", approved=True, enabled=True))
        session.add(ManagedGroup(chat_id=-500, title="Seen only", approved=False, enabled=True))
        await session.commit()
        service = GlobalAdsService(settings, session)
        await service.reconcile(bot)
    schedules = await rows(database, cid)
    future = next((r for r in schedules if r.chat_id == -400), None)
    assert future is None
    assert not any(r.chat_id == -500 for r in schedules)
    async with database() as session:
        with pytest.raises(DomainError):
            await GlobalAdsService(settings, session).selection(
                900, cid, "FUTURE", "forbidden-future"
            )


async def test_defaults_do_not_overwrite_existing_and_explicit_apply_preserves_overrides(
    database, settings, clock
):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        policy = await service.policy(900, cid)
        await service.defaults(900, cid, policy.version, "interval_seconds", 43200)
    assert [r.interval_seconds for r in await rows(database, cid)] == [1800] * 3
    await action(database, settings, cid, bot, "DEFAULTS")
    assert [r.interval_seconds for r in await rows(database, cid)] == [1800, 43200, 43200]
    await action(database, settings, cid, bot, "INHERIT", sid=sid)
    assert [r.interval_seconds for r in await rows(database, cid)] == [43200] * 3


@pytest.mark.parametrize("revoked", ["global", "actor", "bot", "managed", "removed"])
async def test_revocation_between_confirmation_and_item_execution_is_isolated(
    database, settings, clock, revoked
):
    settings.admin_ids, settings.group_owner_id = "900,999", 999
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, "START", "late-revoke", bot)
        await service.confirm(900, op.id, op.version)
        if revoked == "global":
            session.add(Administrator(telegram_id=900, active=False, assigned_by=999))
        elif revoked == "managed":
            (await session.get(ManagedGroup, -200)).enabled = False
        elif revoked == "removed":
            session.add(AuditEvent(actor_id=900, action="group_removed", details={"chat_id": -200}))
        else:
            original = bot.get_chat_member

            async def member(chat, identity):
                result = await original(chat, identity)
                if chat == -200 and identity == (900 if revoked == "actor" else bot.id):
                    result.status = "member"
                return result

            bot.get_chat_member = member
        await session.commit()
        oid = op.id
    await finish(database, settings, oid, bot)
    schedules = await rows(database, cid)
    if revoked == "global":
        assert all(r.status != "ACTIVE" or r.id == sid for r in schedules)
    else:
        assert next(r for r in schedules if r.chat_id == -200).status != "ACTIVE"
        assert next(r for r in schedules if r.chat_id == -300).status == "ACTIVE"


async def test_stale_and_foreign_confirmation_cannot_mutate(database, settings, clock):
    cid, sid, bot = await setup(database, settings, clock)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, "STOP", "stale", bot)
        with pytest.raises(DomainError):
            await service.confirm(1, op.id, op.version)
        policy = await service.policy(900, cid)
        await service.defaults(900, cid, policy.version, "interval_seconds", 7200)
        with pytest.raises(DomainError):
            await service.confirm(900, op.id, op.version)
    assert (await rows(database, cid))[0].status == "ACTIVE"


async def concurrency(sessions, settings, clock):
    cid, sid, bot = await setup(sessions, settings, clock)
    await assign(sessions, settings, cid, bot)
    async with sessions() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, "START", "concurrent", bot)
        await service.confirm(900, op.id, op.version)
        oid = op.id

    async def run():
        async with sessions() as session:
            await GlobalAdsService(settings, session).run(oid, bot)

    await asyncio.wait_for(asyncio.gather(*(run() for _ in range(4))), 20)
    assert all(r.status == "ACTIVE" for r in await rows(sessions, cid))
    workers = [AdsPublisher(bot, settings, sessions) for _ in range(3)]
    await asyncio.wait_for(asyncio.gather(*(w.tick() for w in workers)), 20)
    attempts, _, _ = await snapshot(sessions, settings)
    assert len(attempts) == 3 and all(a.status == "SENT" for a in attempts)
    assert len(bot.calls) == 3


async def test_sqlite_concurrent_bulk_actions_and_workers(database, settings, clock):
    await concurrency(database, settings, clock)


async def test_postgres_concurrent_bulk_actions_and_workers(postgres_contract, clock):
    sessions, settings = postgres_contract
    await concurrency(sessions, settings, clock)


async def test_rate_reservations_are_persistent_and_do_not_consume_api_tries(
    database, settings, clock
):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    settings.ads_global_messages_per_second = 20
    settings.ads_group_messages_per_minute = 20
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert [a.status for a in attempts].count("SENT") == 1
    assert sum(p.tries for p in parts) == 1
    clock[0] += timedelta(seconds=1)
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, _ = await snapshot(database, settings)
    assert [a.status for a in attempts].count("SENT") == 2


async def test_active_content_change_keeps_every_group_schedule(database, settings, clock):
    cid, sid, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    before = [(r.id, r.version, r.next_run_at) for r in await rows(database, cid)]
    async with database() as session:
        ads = AdsService(settings, session)
        draft = await ads.stage(
            900, cid, 2, "TEXT", {"text": "New shared content", "entities": []}, "global-edit"
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
    assert [(r.id, r.version, r.next_run_at) for r in await rows(database, cid)] == before
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 3 and all(c[1]["text"] == "New shared content" for c in bot.calls)


async def test_slow_group_does_not_block_other_pool_slots(database, settings, clock):
    cid, _, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow(kind, args):
        if args["chat_id"] == -100:
            entered.set()
            await release.wait()

    bot.on_send = slow
    async with database() as session:
        await PublicationService(settings, session).enqueue()
    worker = AdsPublisher(bot, settings, database)
    worker.background = True

    async def progress_while_blocked():
        while True:
            await worker.pool_tick()
            attempts, _, _ = await snapshot(database, settings)
            if entered.is_set() and len([a for a in attempts if a.status == "SENT"]) == 2:
                return attempts

    try:
        # Other groups may complete before this task reaches its API await.
        # Synchronize on actual entry, rather than assuming a fixed number of ticks.
        attempts = await asyncio.wait_for(progress_while_blocked(), 10)
        assert len([a for a in attempts if a.status == "SENT"]) == 2
        assert next(a for a in attempts if a.chat_id == -100).status == "SENDING"
    finally:
        release.set()
        await asyncio.gather(*worker.inflight)


async def test_campaigns_are_interleaved_in_enqueue_and_claim(database, settings, clock):
    cid, _, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    second, _, _, _, _ = await configured(database, settings, clock, prefix="other-campaign-")
    async with database() as session:
        service = PublicationService(settings, session)
        assert await service.enqueue(limit=2) == 2
        claims = [await service.claim(), await service.claim()]
        schedules = [
            (
                await session.get(
                    AdSchedule, (await session.get(AdPublicationAttempt, c[0])).schedule_id
                )
            ).campaign_id
            for c in claims
        ]
        assert set(schedules) == {cid, second}


async def test_restarted_bulk_item_is_fenced_and_completed_once(database, settings, clock):
    cid, sid, bot = await setup(database, settings, clock)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, "STOP", "restart-bulk", bot)
        await service.confirm(900, op.id, op.version)
        oid = op.id
        old = await service._claim_item(oid, -100)
    clock[0] += timedelta(seconds=91)
    await finish(database, settings, oid, bot)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        with pytest.raises(DomainError):
            await service._apply(oid, -100, old, bot)
        row = await session.get(AdSchedule, sid, populate_existing=True)
        assert row.status == "STOPPED"


async def test_postgres_independent_schedule_transitions_share_acl_without_exclusive_bottleneck(
    postgres_contract, clock
):
    sessions, settings = postgres_contract
    cid, sid, bot = await setup(sessions, settings, clock)
    await assign(sessions, settings, cid, bot)
    second = (await rows(sessions, cid))[1].id
    first_locked, release = asyncio.Event(), asyncio.Event()

    async def hold():
        async with sessions() as session:
            async with PublicationService(settings, session).transition(cid=cid, sid=sid):
                first_locked.set()
                await release.wait()

    task = asyncio.create_task(hold())
    await first_locked.wait()
    try:
        async with sessions() as session:

            async def other():
                async with PublicationService(settings, session).transition(cid=cid, sid=second):
                    return True

            assert await asyncio.wait_for(other(), 2)
    finally:
        release.set()
        await task


async def test_membership_change_rejects_old_all_groups_confirmation(database, settings, clock):
    cid, _, bot = await setup(database, settings, clock)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.prepare(900, cid, "STOP", "old-membership", bot)
        # A newly reconciled group changes what "all groups" would mean.
        session.add(
            AdSchedule(campaign_id=cid, chat_id=-200, authorized_by=900, override_fields=[])
        )
        await session.commit()
        with pytest.raises(DomainError, match="stale"):
            await service.confirm(900, op.id, op.version)
    assert (await rows(database, cid))[0].status == "ACTIVE"


async def test_postgres_duplicate_preparation_creates_one_operation(postgres_contract, clock):
    sessions, settings = postgres_contract
    cid, _, bot = await setup(sessions, settings, clock)

    async def prepare():
        async with sessions() as session:
            return (
                await GlobalAdsService(settings, session).prepare(
                    900, cid, "STOP", "same-callback", bot
                )
            ).id

    ids = await asyncio.gather(*(prepare() for _ in range(4)))
    assert len(set(ids)) == 1


async def test_group_api_failure_isolated_and_per_group_expiry(database, settings, clock):
    from aiogram.exceptions import TelegramForbiddenError
    from test_ads_publication import METHOD

    cid, _, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    original = bot.send_message

    async def fail_one(chat_id=None, text=None, **args):
        if chat_id == -200:
            raise TelegramForbiddenError(method=METHOD, message="Bot removed")
        return await original(chat_id, text, **args)

    bot.send_message = fail_one
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, _ = await snapshot(database, settings)
    assert {a.chat_id: a.status for a in attempts} == {-100: "SENT", -200: "FAILED", -300: "SENT"}
    async with database() as session:
        for row in await session.scalars(
            select(AdSchedule).where(AdSchedule.campaign_id == cid, AdSchedule.chat_id != -200)
        ):
            row.end_at = clock[0] + timedelta(seconds=1)
        await session.commit()
    clock[0] += timedelta(minutes=30)
    await AdsPublisher(bot, settings, database).tick()
    assert all(r.status == "EXPIRED" for r in await rows(database, cid) if r.chat_id != -200)


async def test_same_chat_rate_gate_is_shared_between_campaigns_after_restart(
    database, settings, clock
):
    await configured(database, settings, clock)
    _, _, bot, _, _ = await configured(database, settings, clock, prefix="second-")
    settings.ads_group_messages_per_minute = 20
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 1
    clock[0] += timedelta(seconds=2)
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 1
    clock[0] += timedelta(seconds=1)
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 2


async def test_expired_future_policy_never_adds_new_groups(database, settings, clock):
    cid, _, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot, mode="EXISTING")
    async with database() as session:
        service = GlobalAdsService(settings, session)
        policy = await service.policy(900, cid)
        await service.defaults(900, cid, policy.version, "end_at", clock[0] + timedelta(seconds=1))
        session.add(ManagedGroup(chat_id=-600, title="Too late", approved=True, enabled=True))
        await session.commit()
    clock[0] += timedelta(seconds=2)
    async with database() as session:
        await GlobalAdsService(settings, session).reconcile(bot)
    assert not any(r.chat_id == -600 for r in await rows(database, cid))


async def test_global_statistics_distinguishes_groups_from_assignments(database, settings, clock):
    await configured(database, settings, clock)
    await configured(database, settings, clock, prefix="another-")
    async with database() as session:
        stats = await GlobalAdsService(settings, session).statistics(900)
        assert stats["campaigns"] == 2 and stats["groups"] == 1 and stats["assignments"] == 2

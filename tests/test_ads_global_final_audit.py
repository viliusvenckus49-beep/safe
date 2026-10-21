"""Fresh adversarial review contracts; all Telegram requests use doubles."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from sqlalchemy import func, select
from test_ads_global import action, assign, finish, rows, setup
from test_ads_global import global_clock as global_clock
from test_ads_global_ui import latest
from test_ads_publication import INSTANT, METHOD, configured, snapshot
from test_ads_publication import clock as clock
from test_ads_publication_ui import members as members
from test_integration_contracts import postgres_contract as postgres_contract
from test_telegram import journey as journey

import app.bot.ads_publisher as publisher_module
from app.ads_global import GlobalAdsService
from app.ads_publication import PublicationService
from app.ads_services import AdsService
from app.bot.ads_publisher import AdsPublisher
from app.bot.global_ads import cb
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t
from app.models import (
    AdAssignmentPolicy,
    AdGlobalOperation,
    AdPublicationAttempt,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
)


async def test_late_response_log_reports_persisted_uncertain_not_sent(
    database, settings, clock, monkeypatch
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    events = []
    monkeypatch.setattr(
        publisher_module, "log", SimpleNamespace(info=lambda event, **data: events.append(data))
    )

    async def recover_during_send(kind, args):
        clock[0] += timedelta(seconds=91)
        async with database() as session:
            assert await PublicationService(settings, session).claim() is None

    bot.on_send = recover_during_send
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == parts[0].status == "UNCERTAIN"
    assert events[-1]["status"] == "UNCERTAIN"
    assert events[-1]["telegram_status"] == "SENT"


@pytest.mark.parametrize("outcome", ["FAILED", "UNCERTAIN", "CANCELLED", "INCOMPLETE_REPLY"])
async def test_partial_statistics_count_attempt_once_and_keep_real_outcome(
    database, settings, clock, outcome
):
    cid, sid, bot, _, _ = await configured(database, settings, clock, album=True)
    if outcome in {"FAILED", "UNCERTAIN"}:
        error = (
            TelegramForbiddenError(method=METHOD, message="bot removed")
            if outcome == "FAILED"
            else TelegramNetworkError(method=METHOD, message="lost response")
        )
        bot.errors = [None, error]
    elif outcome == "CANCELLED":

        async def stop(kind, args):
            if kind == "album":
                async with database() as session:
                    pub = PublicationService(settings, session)
                    row = await pub.one(900, cid)
                    await pub.control(900, cid, row.version, "STOPPED", bot)

        bot.on_send = stop
    else:
        original = bot.send_media_group

        async def incomplete(**args):
            return (await original(**args))[:1]

        bot.send_media_group = incomplete
    await AdsPublisher(bot, settings, database).tick()
    async with database() as session:
        per_group = await PublicationService(settings, session, sid).statistics(900, cid)
        total = await GlobalAdsService(settings, session).statistics(900, cid)
        assert per_group["partial"] == total["partial"] == 1
        assert total["sent"] == 0
        attempt = await session.scalar(select(AdPublicationAttempt))
        assert attempt.status == ("UNCERTAIN" if outcome == "INCOMPLETE_REPLY" else outcome)


async def test_publication_audit_identifies_actual_campaign(database, settings, clock):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    await AdsPublisher(bot, settings, database).tick()
    async with database() as session:
        events = list(
            await session.scalars(
                select(AuditEvent).where(AuditEvent.action.like("ads_publication_%"))
            )
        )
        assert events and all(e.details["campaign_id"] == cid for e in events)


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_default_interval_requires_actor_bound_confirmation(
    journey, database, settings, clock, members, lang
):
    from app.bot.auto_ads import cb as unified_cb
    from app.services import Service

    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
        before = (await GlobalAdsService(settings, session).policy(900, cid)).version
    await journey.click(unified_cb("set_interval", cid, before, 7200), actor=42)
    async with database() as session:
        assert (await GlobalAdsService(settings, session).policy(900, cid)).version == before
    await journey.click(unified_cb("set_interval", cid, before, 7200), actor=900)
    await journey.click(unified_cb("set_interval", cid, before, 86400), actor=900)
    async with database() as session:
        p = await GlobalAdsService(settings, session).policy(900, cid)
        assert (p.version, p.interval_seconds, p.auto_activate) == (before + 1, 7200, False)


async def test_expired_pending_selection_is_retained_but_not_offered(
    journey, database, settings, clock, members
):
    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        op = await GlobalAdsService(settings, session).selection(900, cid, "SELECTED", "expired-ui")
        oid = op.id
        op.expires_at = INSTANT - timedelta(seconds=1)
        await session.commit()
    await journey.click(cb("operations"), actor=900)
    assert oid not in str(latest(journey).reply_markup)
    async with database() as session:
        assert (await session.get(AdGlobalOperation, oid)).status == "SELECTING"


@pytest.mark.parametrize("field", ["start_at", "end_at"])
async def test_default_date_confirmation_uses_vilnius_and_changes_only_after_confirm(
    journey, database, settings, clock, members, field
):
    from app.bot.auto_ads import cb as unified_cb

    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        p = await GlobalAdsService(settings, session).policy(900, cid)
        before = p.version
        original = getattr(p, field)
    await journey.click(unified_cb("set_date", cid, before, field), actor=900)
    async with database() as session:
        assert (
            getattr(await GlobalAdsService(settings, session).policy(900, cid), field) == original
        )
    await journey.send("2026-11-20 12:00", actor=900)
    async with database() as session:
        from app.moderation_services import utc

        p = await GlobalAdsService(settings, session).policy(900, cid)
        assert utc(getattr(p, field)).isoformat() == "2026-11-20T10:00:00+00:00"
        assert p.version == before + 1


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_partial_stats_localization_is_explicit_subset(lang):
    from app.i18n import CATALOGS

    for key in ["g.statistics", "ap.stats_text"]:
        assert "{partial}" in CATALOGS[lang][key]
    assert t("g.default_change_confirm", lang)


async def test_postgres_competing_admin_default_confirmations_are_atomic(postgres_contract, clock):
    sessions, settings = postgres_contract
    cid, sid, _ = await setup(sessions, settings, clock)
    before = (await rows(sessions, cid))[0]
    async with sessions() as session:
        service = GlobalAdsService(settings, session)
        version = (await service.policy(900, cid)).version
        first = await service.prepare_defaults(
            900, cid, version, "interval_seconds", 7200, "owner-a"
        )
        second = await service.prepare_defaults(
            901, cid, version, "interval_seconds", 14400, "owner-b"
        )
        confirmations = [(900, first.id), (901, second.id)]

    async def confirm(actor, oid):
        async with sessions() as session:
            try:
                await GlobalAdsService(settings, session).confirm(actor, oid, 1)
                return "ok"
            except DomainError as error:
                return error.code

    assert sorted(
        await asyncio.wait_for(asyncio.gather(*(confirm(*c) for c in confirmations)), 10)
    ) == ["ap_stale", "ok"]
    async with sessions() as session:
        p = await session.get(AdAssignmentPolicy, cid)
        row = await session.get(AdSchedule, sid)
        assert p.version == version + 1 and p.interval_seconds in {7200, 14400}
        assert (row.version, row.next_run_at, row.interval_seconds) == (
            before.version,
            before.next_run_at,
            before.interval_seconds,
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AdGlobalOperation)
                .where(AdGlobalOperation.status == "DONE")
            )
            == 1
        )


async def test_postgres_rollback_preserves_unsent_intent_and_retry_is_single(
    postgres_contract, clock, monkeypatch
):
    sessions, settings = postgres_contract
    cid, _, bot, _, _ = await configured(sessions, settings, clock)
    async with sessions() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        claim = await pub.claim()

        def fail(*args, **kwargs):
            raise RuntimeError("transaction abort before send")

        monkeypatch.setattr(pub.ads, "audit", fail)
        with pytest.raises(RuntimeError, match="transaction abort"):
            await pub.begin(*claim)
    attempts, parts, _ = await snapshot(sessions, settings)
    assert attempts[0].status == "CLAIMED" and parts[0].status == "PENDING"
    assert parts[0].tries == 0 and bot.calls == []
    await AdsPublisher(bot, settings, sessions).execute(claim)
    assert len(bot.calls) == 1
    async with sessions() as session:
        stats = await GlobalAdsService(settings, session).statistics(900, cid)
        assert stats["sent"] == 1 and stats["partial"] == 0
    partial_cid, _, partial_bot, _, _ = await configured(
        sessions, settings, clock, album=True, prefix="postgres-partial-"
    )
    partial_bot.errors = [None, TelegramNetworkError(method=METHOD, message="lost companion")]
    await AdsPublisher(partial_bot, settings, sessions).tick()
    async with sessions() as session:
        stats = await GlobalAdsService(settings, session).statistics(900, partial_cid)
        assert stats["sent"] == 0 and stats["uncertain"] == stats["partial"] == 1


@pytest.mark.parametrize("change", ["policy", "removed", "archived", "expired"])
async def test_reconcile_rechecks_changes_during_telegram_read(database, settings, clock, change):
    from app.ads_unified import UnifiedAds

    cid, _, bot = await setup(database, settings, clock)
    settings.group_owner_id = 900
    await assign(database, settings, cid, bot, mode="EXISTING")
    async with database() as session:
        session.add(ManagedGroup(chat_id=-400, title="New", approved=True, enabled=True))
        await session.commit()
        expected = (await GlobalAdsService(settings, session).policy(900, cid)).version
    original = bot.get_chat_member
    touched = False

    async def read(chat, identity):
        nonlocal touched
        if chat == -400 and not touched:
            touched = True
            async with database() as session:
                if change in {"policy", "expired"}:
                    service = GlobalAdsService(settings, session)
                    p = await service.policy(900, cid)
                    field = "interval_seconds" if change == "policy" else "end_at"
                    await service.defaults(
                        900,
                        cid,
                        p.version,
                        field,
                        7200 if change == "policy" else INSTANT + timedelta(seconds=2),
                    )
                elif change == "removed":
                    await GroupService(settings, session).remove_group(900, chat)
                else:
                    ads = AdsService(settings, session)
                    c = await ads.get(900, cid)
                    await ads.archive(900, cid, c.version)
        return await original(chat, identity)

    bot.get_chat_member = read
    async with database() as session:
        with pytest.raises(DomainError):
            await UnifiedAds(settings, session).groups(900, cid, expected, [-400], bot)
        assert (
            await session.scalar(
                select(func.count()).select_from(AdSchedule).where(AdSchedule.chat_id == -400)
            )
            == 0
        )
    assert touched


async def test_postgres_duplicate_future_reconcile_and_explicit_readd_preserve_history(
    postgres_contract, clock
):
    sessions, settings = postgres_contract
    cid, sid, bot = await setup(sessions, settings, clock)
    await assign(sessions, settings, cid, bot, mode="EXISTING")
    await action(sessions, settings, cid, bot, "START")
    await AdsPublisher(bot, settings, sessions).tick()
    await action(sessions, settings, cid, bot, "REMOVE", sid=sid)
    async with sessions() as session:
        session.add(ManagedGroup(chat_id=-400, title="New", approved=True, enabled=True))
        await session.commit()

    async def reconcile():
        async with sessions() as session:
            await GlobalAdsService(settings, session).reconcile(bot)

    await asyncio.wait_for(asyncio.gather(reconcile(), reconcile()), 15)
    assert next(r for r in await rows(sessions, cid) if r.id == sid).excluded
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AdSchedule).where(AdSchedule.chat_id == -400)
            )
            == 0
        )
    await assign(
        sessions, settings, cid, bot, chats=(-100, -200, -300, -400), request="explicit-readd"
    )
    restored = next(r for r in await rows(sessions, cid) if r.id == sid)
    assert not restored.excluded and restored.status == "DRAFT"
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AdPublicationAttempt)) == 3
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AdSchedule)
                .where(AdSchedule.campaign_id == cid, AdSchedule.chat_id == -100)
            )
            == 1
        )


async def test_bulk_pause_during_send_does_not_stop_other_campaign(database, settings, clock):
    cid, _, bot = await setup(database, settings, clock)
    await assign(database, settings, cid, bot)
    await action(database, settings, cid, bot, "START")
    other, _, _, _, _ = await configured(database, settings, clock, prefix="overlap-")
    paused = False

    async def pause(kind, args):
        nonlocal paused
        if not paused:
            paused = True
            await action(database, settings, cid, bot, "PAUSE", request="pause-mid-send")

    bot.on_send = pause
    await AdsPublisher(bot, settings, database).tick()
    assert all(r.status == "PAUSED" for r in await rows(database, cid))
    async with database() as session:
        first = await GlobalAdsService(settings, session).statistics(900, cid)
        second = await GlobalAdsService(settings, session).statistics(900, other)
        assert first["sent"] == 1 and second["sent"] == 1


async def test_redis_connection_failure_does_not_block_sql_operation_recovery(
    database, settings, clock
):
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError

    # An isolated TCP peer closes the Redis connection; no service/production restart.
    async def disconnect(reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(disconnect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    client = Redis(host="127.0.0.1", port=port, socket_timeout=0.2)
    try:
        with pytest.raises(ConnectionError):
            await client.ping()
        cid, _, bot = await setup(database, settings, clock)
        await assign(database, settings, cid, bot)
        async with database() as session:
            service = GlobalAdsService(settings, session)
            op = await service.prepare(900, cid, "START", "redis-outage", bot)
            await service.confirm(900, op.id, op.version)
            oid = op.id
        await finish(database, settings, oid, bot)
        await AdsPublisher(bot, settings, database).tick()
        async with database() as session:
            assert (await session.get(AdGlobalOperation, oid)).status == "DONE"
            assert (await GlobalAdsService(settings, session).statistics(900, cid))["sent"] == 3
    finally:
        await client.aclose()
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("revocation", ["managed_group", "local_admin"])
async def test_revocation_during_album_keeps_known_ids_without_sending_companion(
    database, settings, clock, revocation
):
    cid, sid, bot, _, _ = await configured(database, settings, clock, album=True)
    settings.group_owner_id = 900

    async def revoke(kind, args):
        if kind == "album":
            if revocation == "managed_group":
                async with database() as session:
                    await GroupService(settings, session).remove_group(900, -100)
            else:
                bot.roles[900] = "member"

    bot.on_send = revoke
    await AdsPublisher(bot, settings, database).tick()
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == ("CANCELLED" if revocation == "managed_group" else "FAILED")
    assert parts[0].status == "SENT" and parts[0].message_ids == [100, 101]
    assert parts[1].message_ids == [] and len([c for c in bot.calls if c[1]["chat_id"] < 0]) == 1
    async with database() as session:
        assert (await PublicationService(settings, session, sid).statistics(900, cid))[
            "partial"
        ] == 1


@pytest.mark.parametrize("state", ["archived", "expired", "next_after_end"])
async def test_statistics_never_advertise_ineligible_next_publication(
    database, settings, clock, state
):
    cid, sid, _, _, _ = await configured(database, settings, clock)
    async with database() as session:
        if state == "archived":
            ads = AdsService(settings, session)
            campaign = await ads.get(900, cid)
            await ads.archive(900, cid, campaign.version)
        else:
            row = await session.get(AdSchedule, sid)
            row.end_at = INSTANT + timedelta(seconds=10)
            if state == "expired":
                clock[0] += timedelta(seconds=11)
            else:
                row.next_run_at = INSTANT + timedelta(minutes=30)
            await session.commit()
        stats = await GlobalAdsService(settings, session).statistics(900, cid)
        assert stats["next"] is None
        assert (await PublicationService(settings, session, sid).statistics(900, cid))[
            "next"
        ] is None
        if state != "next_after_end":
            assert stats["active"] == 0

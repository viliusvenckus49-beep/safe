"""Withdraw a removed group's audience without rewriting delivery evidence."""

from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError
from aiogram.methods import GetChatMember
from sqlalchemy import func, select
from test_ads_publication import clock as clock
from test_ads_publication import configured
from test_integration_contracts import postgres_contract as postgres_contract

from app.ads_audience import prune_unavailable_groups
from app.ads_publication import PublicationService
from app.bot.ads_publisher import AdsPublisher
from app.group_services import GroupService
from app.models import (
    AdPublicationAttempt,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
)


@pytest.mark.parametrize("state", ["PENDING", "CLAIMED", "SENDING", "UNCERTAIN", "SENT"])
async def test_detach_is_atomic_scoped_idempotent_and_preserves_delivery_evidence(
    database, settings, clock, state
):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    other_cid, other_sid, _, _, _ = await configured(database, settings, clock, prefix="other-")
    async with database() as session:
        session.add(ManagedGroup(chat_id=-200, title="Other group", approved=True, enabled=True))
        await session.flush()
        session.add(
            AdSchedule(
                campaign_id=cid,
                chat_id=-200,
                status="ACTIVE",
                interval_seconds=1800,
                start_at=clock[0],
                next_run_at=clock[0] + timedelta(hours=1),
                authorized_by=900,
            )
        )
        await session.commit()
        pub = PublicationService(settings, session)
        await pub.enqueue()
        aid = await session.scalar(
            select(AdPublicationAttempt.id).where(AdPublicationAttempt.schedule_id == sid)
        )
        versions = {r.id: r.version for r in await session.scalars(select(AdSchedule))}
    if state != "PENDING":
        async with database() as session:
            claim = await PublicationService(settings, session).claim()
        assert claim[0] == aid
        if state in {"SENDING", "UNCERTAIN"}:
            async with database() as session:
                prepared = await PublicationService(settings, session).begin(*claim)
                if state == "UNCERTAIN":
                    await PublicationService(settings, session).result(
                        *claim, prepared[1].id, "UNCERTAIN", None, "TelegramNetworkError"
                    )
        elif state == "SENT":
            await AdsPublisher(bot, settings, database).execute(claim)
    async with database() as session:
        await GroupService(settings, session).disable_group(-100)
    async with database() as session:
        rows = list(await session.scalars(select(AdSchedule)))
        for row in rows:
            if row.chat_id == -100:
                assert row.excluded and row.status == "STOPPED" and row.next_run_at is None
                assert row.version == versions[row.id] + 1
            else:
                assert (
                    not row.excluded and row.status == "ACTIVE" and row.version == versions[row.id]
                )
        attempt = await session.get(AdPublicationAttempt, aid)
        assert attempt.status == ("CANCELLED" if state in {"PENDING", "CLAIMED"} else state)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "ads_group_detached")
            )
            == 2
        )
        await GroupService(settings, session).disable_group(-100)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "ads_group_detached")
            )
            == 2
        )
        assert await prune_unavailable_groups(session, settings) == 0


async def test_legacy_removed_groups_are_detached_without_reactivating_on_return(
    database, settings, clock
):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        group = await session.get(ManagedGroup, -100)
        group.enabled = False
        await session.commit()
        assert await prune_unavailable_groups(session, settings) == 1
        group = await session.get(ManagedGroup, -100)
        group.enabled = True
        await session.commit()
        assert await prune_unavailable_groups(session, settings) == 0
        row = await session.get(AdSchedule, sid, populate_existing=True)
        assert row.excluded and row.status == "STOPPED"


@pytest.mark.parametrize("mode", ["left", "kicked", "forbidden", "network", "present"])
async def test_only_confirmed_bot_absence_detaches(database, settings, clock, mode):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    if mode in {"left", "kicked"}:
        bot.roles[bot.id] = mode
    elif mode == "forbidden":
        bot.read_error = TelegramForbiddenError(
            method=GetChatMember(chat_id=-100, user_id=bot.id),
            message="bot was kicked from the supergroup chat",
        )
    elif mode == "network":
        bot.read_error = TelegramNetworkError(
            method=GetChatMember(chat_id=-100, user_id=bot.id), message="connection lost"
        )
    absent = await AdsPublisher(bot, settings, database).detach_absent_bot(-100)
    assert absent == (mode in {"left", "kicked", "forbidden"})
    async with database() as session:
        row = await session.get(AdSchedule, sid)
        assert row.excluded == absent
        assert row.status == ("STOPPED" if absent else "ACTIVE")


async def test_postgres_concurrent_detach_fences_claim_without_duplicate_audit(
    postgres_contract, clock
):
    import asyncio

    sessions, settings = postgres_contract
    cid, sid, bot, _, _ = await configured(sessions, settings, clock)
    async with sessions() as session:
        await PublicationService(settings, session).enqueue()
        claim = await PublicationService(settings, session).claim()

    async def withdraw():
        async with sessions() as session:
            await GroupService(settings, session).disable_group(-100)

    await asyncio.gather(withdraw(), withdraw())
    async with sessions() as session:
        assert await PublicationService(settings, session).begin(*claim) is None
        row = await session.get(AdSchedule, sid)
        assert row.excluded and row.status == "STOPPED"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "ads_group_detached")
            )
            == 1
        )
    assert bot.calls == []

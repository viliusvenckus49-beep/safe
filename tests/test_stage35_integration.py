"""Cross-module transactions: customer/publisher cannot mutate SAFECheck/moderation."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_ads_publication import clock as clock
from test_ads_publication import configured
from test_group_moderation_foundation import NativeBot, setup_groups, submit
from test_integration_contracts import postgres_contract as postgres_contract

from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_publication import PublicationService
from app.bot.ads_publisher import AdsPublisher
from app.bot.local_moderation import LocalModerationExecutor
from app.errors import DomainError
from app.models import (
    AdCampaign,
    AdPublicationAttempt,
    AdSchedule,
    GroupSanction,
    GroupSanctionJob,
    ProfileActivity,
    ReputationEvent,
    ScamRecord,
    TrustedDesignation,
    User,
    UsernameHistory,
)
from app.services import Service


async def protected_rows(sessions):
    models = (
        User,
        ReputationEvent,
        ScamRecord,
        TrustedDesignation,
        ProfileActivity,
        UsernameHistory,
        GroupSanction,
        GroupSanctionJob,
    )
    async with sessions() as session:
        return {
            model.__tablename__: sorted(
                [
                    tuple(getattr(row, c.name) for c in model.__table__.columns)
                    for row in await session.scalars(select(model))
                ],
                key=repr,
            )
            for model in models
        }


@pytest.mark.parametrize("revoke", [False, True])
async def test_full_customer_to_publication_path_keeps_core_and_local_sanctions(
    postgres_contract, clock, revoke
):
    sessions, settings = postgres_contract
    local_bot = NativeBot()
    await setup_groups(sessions, local_bot)
    local = await submit(sessions, local_bot)
    await LocalModerationExecutor(sessions, local_bot).process(sanction_id=local.id)
    async with sessions() as session:
        core = Service(settings, session)
        await core.observe(41, "giver", "First")
        await core.observe(42, "customer", "Customer <&>")
        vote = await core.vote(41, "42", 1, None, "integration-rep", comment="Verified trade")
        await core.moderate_reputation(900, vote["reputation_request"].reference, True)
        await core.add_scam(900, "61", "Separate known scam")
        await core.set_trusted(900, "62", True, "integration-trusted")
        user = await core.resolve("42")
        session.add(
            ProfileActivity(
                chat_id=-100, message_id=991, user_id=user.id, active_date=clock[0].date()
            )
        )
        await session.commit()
        assert (await core.profile("42"))["score"] == 1
    cid, sid, bot, _, revision = await configured(sessions, settings, clock)
    async with sessions() as session:
        grant = await CustomerAccess(settings, session).assign(900, cid, 42, "0.0")
        gid = grant.id
        token = await CustomerAccess(settings, session).link(900, cid, f"{gid}.1")
        assert await CustomerAccess(settings, session).resolve(42, token) == cid
        with pytest.raises(DomainError):
            await CustomerAccess(settings, session).resolve(43, token)
        await session.rollback()
    before = await protected_rows(sessions)
    async with sessions() as session:
        await PublicationService(settings, session).enqueue()
        schedule = await session.get(AdSchedule, sid)
        expected = (schedule.version, schedule.interval_seconds, schedule.next_run_at)
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42,
            cid,
            revision,
            "TEXT",
            {"text": "Customer changed 🍒", "entities": []},
            "integration-edit",
        )
        did = draft.id
        await ads.mark_previewed(42, did)
    if revoke:
        async with sessions() as session:
            await CustomerAccess(settings, session).revoke(900, cid, f"{gid}.2")
    async with sessions() as session:
        ads = CustomerAdsService(settings, session)
        if revoke:
            with pytest.raises(DomainError):
                await ads.save(42, did)
        else:
            await ads.save(42, did)
        row = await session.get(AdSchedule, sid)
        assert (row.version, row.interval_seconds, row.next_run_at) == expected
    await AdsPublisher(bot, settings, sessions).tick()
    assert bot.calls[0][1]["text"].startswith("Old")  # Already pinned attempt.
    clock[0] += timedelta(minutes=30)
    await AdsPublisher(bot, settings, sessions).tick()
    assert bot.calls[-1][1]["text"] == ("Old <literal> 🍒" if revoke else "Customer changed 🍒")
    assert await protected_rows(sessions) == before
    async with sessions() as session:
        attempts = list(await session.scalars(select(AdPublicationAttempt)))
        assert len(attempts) == 2 and all(r.status == "SENT" for r in attempts)
        assert (await session.get(AdCampaign, cid)).version == revision + (not revoke)
        stats = await PublicationService(settings, session).statistics(900, cid)
        assert (stats["sent"], stats["failed"], stats["uncertain"], stats["partial"]) == (
            2,
            0,
            0,
            0,
        )

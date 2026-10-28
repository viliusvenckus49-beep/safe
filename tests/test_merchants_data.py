"""Phase B: real SQL filtering and common REP/SCAM semantics, no production seeding."""

from datetime import UTC, datetime

from app.merchant_catalog import grams, normalized
from app.merchant_repository import MerchantRepository
from app.models import (
    MerchantEntry,
    MerchantSearchGram,
    ReputationAdjustment,
    ReputationRequest,
    ScamRecord,
)
from app.repositories import Repository
from app.services import Service


async def merchant(session, settings, tg, name, city="vilnius"):
    core = Service(settings, session)
    user = await core.observe(tg, "seller" + str(tg), "Seller")
    entry = MerchantEntry(
        owner_user_id=user.id,
        name=name,
        name_key=normalized(name),
        description="Lawful professional service",
        city_code=city,
        status="APPROVED",
        approved_by=900,
        approved_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [MerchantSearchGram(entry_id=entry.id, token=token) for token in grams(entry.name_key)]
    )
    await session.commit()
    return entry, user


async def test_real_city_pagination_indexed_partial_search_and_username(database, settings):
    async with database() as session:
        for i in range(8):
            await merchant(session, settings, 100 + i, "North Studio " + str(i))
        await merchant(session, settings, 200, "Other Studio", "kaunas")
        repo = MerchantRepository(session)
        rows, total, page = await repo.listing(city="vilnius")
        assert total == 8 and len(rows) == 6 and page == 0
        rows, total, page = await repo.listing(city="vilnius", page=99)
        assert len(rows) == 2 and page == 1
        rows, total, page = await repo.listing(query="rth stud")
        assert total == 8
        rows, total, page = await repo.listing(query="seller200")
        assert total == 1 and rows[0][1].telegram_id == 200
        rows, total, page = await repo.listing(city="pajurys")
        assert total == 0 and rows == []


async def test_sort_and_stats_share_all_existing_reputation_sources(database, settings):
    async with database() as session:
        entry, user = await merchant(session, settings, 42, "Beta Studio")
        await merchant(session, settings, 43, "Alpha Studio")
        giver = await Service(settings, session).observe(44, None, "Reviewer")
        session.add(
            ReputationRequest(
                reference="RP-merchant",
                giver_user_id=giver.id,
                receiver_user_id=user.id,
                value=1,
                request_key="m-review",
                status="APPROVED",
                comment="Excellent service",
            )
        )
        session.add(
            ReputationAdjustment(
                target_id=user.id,
                actor_id=900,
                operation="ADJUST",
                positive_delta=2,
                negative_delta=0,
                reason="Approved correction",
                request_key="m-adjust",
            )
        )
        await session.commit()
        rows, total, page = await MerchantRepository(session).listing()
        assert rows[0][0].id == entry.id
        assert rows[0][2] == (await Repository(session).rep_stats(user.id))[0] == 3
        reviews, total, page = await MerchantRepository(session).reviews(user.id)
        assert total == 1 and reviews[0]["comment"] == "Excellent service"


async def test_scam_exclusion_includes_existing_unresolved_identity_policy(database, settings):
    async with database() as session:
        entry, user = await merchant(session, settings, 42, "Studio")
        unknown = await Service(settings, session).resolve("@othername")
        unknown.username = user.username
        session.add(
            ScamRecord(
                target_id=unknown.id,
                username_snapshot=user.username,
                reason="Approved fraud report",
                moderator_id=900,
            )
        )
        await session.commit()
        assert await Repository(session).has_scam_identity(user)
        assert await MerchantRepository(session).public(entry.id) is None
        assert (await MerchantRepository(session).listing())[1] == 0
        assert (await MerchantRepository(session).listing(admin=True))[1] == 1


async def test_only_approved_reviews_and_sql_sign_filters(database, settings):
    async with database() as session:
        entry, user = await merchant(session, settings, 42, "Studio")
        for i, status in enumerate(("PENDING", "REJECTED", "APPROVED", "APPROVED")):
            giver = await Service(settings, session).observe(100 + i, None, "Reviewer")
            session.add(
                ReputationRequest(
                    reference="RP-" + str(i),
                    giver_user_id=giver.id,
                    receiver_user_id=user.id,
                    value=(-1 if i == 3 else 1),
                    request_key="review-" + str(i),
                    status=status,
                    comment="Private or approved review",
                )
            )
        await session.commit()
        repo = MerchantRepository(session)
        assert (await repo.reviews(user.id))[1] == 2
        assert (await repo.reviews(user.id, "pos"))[1] == 1
        assert (await repo.reviews(user.id, "neg"))[1] == 1

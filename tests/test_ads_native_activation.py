"""Saved native input can activate and publish; Telegram transport is simulated."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from test_ads_publication import Bot, configured
from test_ads_publication import clock as clock
from test_auto_ads_unified import assign, create, groups
from test_integration_contracts import postgres_contract as postgres_contract

import app.ads_unified as unified_module
from app.ads_content import parse_buttons
from app.ads_customers import CustomerAdsService
from app.ads_publication import PublicationService
from app.ads_services import AdsService
from app.ads_unified import UnifiedAds
from app.bot.ads_publisher import AdsPublisher
from app.errors import DomainError
from app.models import AdContentDraft, AdPublicationAttempt, AuditEvent
from app.moderation_services import utc

VALUES = {
    "TEXT": {"text": "New native content", "entities": []},
    "MEDIA": [{"kind": "photo", "file_id": "new_photo"}],
    "BUTTONS": parse_buttons("Open - https://example.com"),
}


async def native_save(database, settings, *, field="TEXT", customer=False, request="new"):
    cid = await create(database, settings, request=request)
    if customer:
        await assign(database, settings, cid)
    async with database() as session:
        ads = (CustomerAdsService if customer else AdsService)(settings, session)
        actor = 42 if customer else 900
        draft = await ads.stage(actor, cid, 2, field, VALUES[field], request + "-native")
        version = await ads.save_received(actor, draft.id)
        assert not draft.previewed
        return cid, draft.id, version.id


async def activation_journey(database, settings, clock, monkeypatch, customer, field):
    monkeypatch.setattr(unified_module, "now", lambda: clock[0])
    settings.ads_global_messages_per_second = 0
    settings.ads_group_messages_per_minute = 0
    cid, _, vid = await native_save(database, settings, customer=customer, field=field)
    await groups(database)
    bot = Bot()
    async with database() as session:
        management = UnifiedAds(settings, session)
        policy = await management.policy(900, cid)
        await management.groups(900, cid, policy.version, [-100], bot)
        policy = await management.policy(900, cid)
        await management.lifecycle(900, cid, policy.version, "START", bot)
        row = (await management.schedules(cid))[0]
        assert row.status == "ACTIVE" and utc(row.next_run_at) > clock[0]
        due = utc(row.next_run_at)
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    assert not bot.calls  # Activation/save never adds an immediate publication.
    clock[0] = due + timedelta(seconds=1)
    await worker.tick()
    await worker.tick()
    async with database() as session:
        attempts = list(await session.scalars(select(AdPublicationAttempt)))
        assert len(attempts) == 1
        assert attempts[0].status == "SENT" and attempts[0].content_version_id == vid


@pytest.mark.parametrize("customer", [False, True])
@pytest.mark.parametrize("field", VALUES)
async def test_native_save_activates_and_sends_once_at_next_slot(
    database, settings, clock, monkeypatch, customer, field
):
    await activation_journey(database, settings, clock, monkeypatch, customer, field)


async def test_postgres_native_activation_and_scheduler(postgres_contract, clock, monkeypatch):
    factory, settings = postgres_contract
    await activation_journey(factory, settings, clock, monkeypatch, True, "TEXT")


@pytest.mark.parametrize("customer", [False, True])
async def test_active_ad_native_edit_preserves_slot_and_pinned_publication(
    database, settings, clock, customer
):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    if customer:
        await assign(database, settings, cid)
    async with database() as session:
        pub = PublicationService(settings, session)
        schedule = await pub.one(900, cid)
        assert await pub.enqueue() == 1
        next_slot = utc(schedule.next_run_at)
        ads = (CustomerAdsService if customer else AdsService)(settings, session)
        actor = 42 if customer else 900
        draft = await ads.stage(actor, cid, 2, "TEXT", VALUES["TEXT"], "active-edit")
        version = await ads.save_received(actor, draft.id)
        vid = version.id
        schedule = await pub.one(900, cid)
        assert schedule.status == "ACTIVE" and utc(schedule.next_run_at) == next_slot
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    assert bot.calls[0][1]["text"].startswith("Old")
    clock[0] = next_slot + timedelta(seconds=1)
    await worker.tick()
    await worker.tick()
    assert bot.calls[1][1]["text"] == VALUES["TEXT"]["text"]
    async with database() as session:
        attempts = list(
            await session.scalars(select(AdPublicationAttempt).order_by(AdPublicationAttempt.id))
        )
        assert len(attempts) == 2 and all(a.status == "SENT" for a in attempts)
        assert attempts[1].content_version_id == vid != attempts[0].content_version_id


@pytest.mark.parametrize(
    "wrong",
    [
        "missing",
        "campaign_id",
        "revision",
        "draft_id",
        "field",
        "actor",
        "validation",
        "request",
        "restore",
    ],
)
async def test_native_evidence_must_match_exact_saved_revision(database, settings, wrong):
    cid, did, _ = await native_save(database, settings)
    async with database() as session:
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "ads_content_saved",
                AuditEvent.details["draft_id"].as_string() == did,
            )
        )
        assert event is not None
        if wrong == "missing":
            await session.delete(event)
        elif wrong == "actor":
            event.actor_id = 123
        elif wrong == "request":
            _, version = await AdsService(settings, session).current(900, cid)
            version.request_key = "another-request"
        elif wrong == "restore":
            (await session.get(AdContentDraft, did)).field = "RESTORE"
            event.details = {**event.details, "field": "RESTORE"}
        else:
            event.details = {
                **event.details,
                wrong: 999 if wrong in {"campaign_id", "revision"} else "wrong",
            }
        await session.commit()
        campaign = await AdsService(settings, session).get(900, cid)
        with pytest.raises(DomainError, match="ap_preview"):
            await PublicationService(settings, session).validated_content(campaign)


@pytest.mark.parametrize("failure", ["dates", "bot_rights", "admin_rights", "group"])
async def test_native_activation_retains_validity_and_rights_checks(
    database, settings, clock, monkeypatch, failure
):
    monkeypatch.setattr(unified_module, "now", lambda: clock[0])
    cid, _, _ = await native_save(database, settings)
    await groups(database)
    bot = Bot()
    async with database() as session:
        management = UnifiedAds(settings, session)
        policy = await management.policy(900, cid)
        await management.groups(900, cid, policy.version, [-100], bot)
        policy = await management.policy(900, cid)
        if failure == "dates":
            await management.change(
                900, cid, policy.version, "end_at", clock[0] - timedelta(days=1), bot
            )
        elif failure in {"bot_rights", "admin_rights"}:
            bot.roles[bot.id if failure == "bot_rights" else 900] = "member"
        else:
            from app.models import ManagedGroup

            (await session.get(ManagedGroup, -100)).approved = False
            await session.commit()
        policy = await management.policy(900, cid)
        with pytest.raises(
            DomainError,
            match={
                "dates": "ap_dates",
                "bot_rights": "ap_rights",
                "admin_rights": "ap_rights",
                "group": "ap_group",
            }[failure],
        ):
            await management.lifecycle(900, cid, policy.version, "START", bot)
    async with database() as session:
        assert (await UnifiedAds(settings, session).schedules(cid))[0].status != "ACTIVE"
    assert not bot.calls

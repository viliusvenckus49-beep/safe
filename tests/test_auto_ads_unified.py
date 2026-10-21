"""Real SQL per-ad contracts; Telegram calls are simulated, never live deliveries."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message, MessageEntity
from sqlalchemy import func, select
from test_ads_publication import Bot, configured
from test_ads_publication import clock as clock
from test_integration_contracts import postgres_contract as postgres_contract

import app.ads_invitations as invitation_module
from app.ads_content import EMPTY, media_from_message, text_from_message, validate_content
from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_global import GlobalAdsService
from app.ads_invitations import ClientInvitations
from app.ads_publication import PublicationService
from app.ads_schedule import INTERVALS, parts_for
from app.ads_services import AdsService
from app.ads_unified import UnifiedAds
from app.bot.ads_publisher import send_part
from app.errors import DomainError
from app.models import (
    AdCampaign,
    AdClientInvitation,
    AdContentVersion,
    AdCustomerGrant,
    AdPublicationAttempt,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
    now,
)
from app.moderation_services import utc


async def create(sessions, settings, name="REDSAFE PARTNER", request="new"):
    async with sessions() as session:
        ads = AdsService(settings, session)
        campaign = await ads.create(900, name, request)
        draft = await ads.stage(
            900, campaign.id, 1, "TEXT", {"text": name, "entities": []}, request + "-text"
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
        return campaign.id


async def groups(sessions):
    async with sessions() as session:
        session.add_all(
            [
                ManagedGroup(chat_id=-100, title="Group A", approved=True),
                ManagedGroup(chat_id=-200, title="Group B", approved=True),
            ]
        )
        await session.commit()


async def assign(sessions, settings, cid, customer=42):
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        return await access.assign(900, cid, customer, access.stamp(await access.latest(cid)))


async def invite(sessions, settings, cid):
    async with sessions() as session:
        service = ClientInvitations(settings, session)
        return await service.issue(900, cid, service.grants.stamp(await service.grants.latest(cid)))


async def test_named_creation_draft_and_independent_content(database, settings):
    first = await create(database, settings)
    second = await create(database, settings, "VIP REKLAMA 01", "second")
    async with database() as session:
        service = UnifiedAds(settings, session)
        assert (await service.ads.get(900, first)).internal_name == "REDSAFE PARTNER"
        assert (await service.ads.current(900, second))[1].content["text"] == "VIP REKLAMA 01"
        assert await service.counts(900) == (2, 0, 2)
        assert await session.scalar(select(func.count()).select_from(AdSchedule)) == 0


async def test_real_search_filters_literal_wildcards_and_four_row_pages(database, settings):
    ids = [await create(database, settings, f"PARTNER {n}", str(n)) for n in range(6)]
    special = await create(database, settings, "100% real_Ad", "special")
    async with database() as session:
        service = UnifiedAds(settings, session)
        assert len(await service.listing(900)) == 5  # Four shown plus next-page sentinel.
        assert len(await service.listing(900, page=1)) == 3
        assert [r.id for r in await service.listing(900, query="% real_")] == [special]
        assert await service.listing(900, status="active") == []
        session.add(ManagedGroup(chat_id=-100, title="Approved", approved=True))
        await session.flush()
        session.add(
            AdSchedule(
                campaign_id=ids[0],
                chat_id=-100,
                status="ACTIVE",
                authorized_by=900,
                interval_seconds=3600,
                start_at=now(),
                next_run_at=now() + timedelta(hours=1),
            )
        )
        await session.commit()
        assert [r.id for r in await service.listing(900, status="active")] == [ids[0]]
        assert ids[0] not in [
            r.id for r in await service.listing(900, status="paused", query="PARTNER")
        ]


async def test_group_snapshot_does_not_expand_or_change_another_ad(database, settings):
    a = await create(database, settings)
    b = await create(database, settings, "B", "b")
    await groups(database)
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, a)
        await service.groups(900, a, policy.version, [-100, -200], Bot(), all_groups=True)
        assert len(await service.schedules(a)) == 2
        assert await service.schedules(b) == []
        session.add(ManagedGroup(chat_id=-300, title="Later group", approved=True))
        await session.commit()
        await GlobalAdsService(settings, session).reconcile(Bot())
        assert {r.chat_id for r in await service.schedules(a)} == {-100, -200}
        assert all(r.status == "DRAFT" for r in await service.schedules(a))


@pytest.mark.parametrize("seconds", INTERVALS)
async def test_individual_intervals_do_not_send_immediately_or_modify_other_ads(
    database, settings, seconds
):
    a = await create(database, settings)
    b = await create(database, settings, "B", "b")
    await groups(database)
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, a)
        await service.groups(900, a, policy.version, [-100], Bot())
        policy = await service.policy(900, a)
        await service.lifecycle(900, a, policy.version, "START", Bot())
        policy = await service.policy(900, a)
        await service.change(900, a, policy.version, "interval_seconds", seconds, Bot())
        row = (await service.schedules(a))[0]
        assert row.interval_seconds == seconds and utc(row.next_run_at) > now()
        assert await service.pub.enqueue() == 0
        assert (await service.policy(900, b)).interval_seconds == 3600
        assert await service.schedules(b) == []


async def test_pause_resume_delete_preserves_content_history_and_revokes_client(database, settings):
    cid = await create(database, settings)
    await groups(database)
    await assign(database, settings, cid)
    payload = await invite(database, settings, cid)
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, cid)
        await service.groups(900, cid, policy.version, [-100], Bot())
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "START", Bot())
        assert live_status(await service.schedules(cid)) == ["ACTIVE"]
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "PAUSE", Bot())
        assert (await service.schedules(cid))[0].next_run_at is None
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "START", Bot())
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "DELETE", Bot())
        assert (await session.get(AdCampaign, cid)).status == "ARCHIVED"
        assert (await service.grants.latest(cid)).status == "REVOKED"
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 2
        assert await service.history(900, cid)
        assert (await service.schedules(cid))[0].status == "STOPPED"
        with pytest.raises(DomainError):
            await ClientInvitations(settings, session).consume(42, payload)


def live_status(rows):
    return [r.status for r in rows]


async def test_expiry_stops_delivery_without_deleting_content(database, settings, monkeypatch):
    cid = await create(database, settings)
    await groups(database)
    instant = now()
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, cid)
        await service.change(900, cid, policy.version, "end_at", instant + timedelta(days=1), Bot())
        policy = await service.policy(900, cid)
        await service.groups(900, cid, policy.version, [-100], Bot())
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "START", Bot())
        monkeypatch.setattr("app.ads_publication.now", lambda: instant + timedelta(days=2))
        await service.pub.enqueue()
        assert (await service.schedules(cid))[0].status == "EXPIRED"
        assert (await service.ads.current(900, cid))[1].content["text"] == "REDSAFE PARTNER"


async def test_invitation_is_hashed_expiring_single_use_and_grant_scoped(database, settings):
    cid = await create(database, settings)
    payload = await invite(database, settings, cid)
    assert len(payload) <= 64
    async with database() as session:
        row = await session.scalar(select(AdClientInvitation))
        assert row.token_hash == sha256(payload[3:].encode()).hexdigest()
        assert payload[3:] not in str(row.__dict__)
        assert await ClientInvitations(settings, session).consume(42, payload) == cid
    async with database() as session:
        access = CustomerAccess(settings, session)
        grant = await access.check(42, cid)
        selector = f"ae_{cid}_{grant.id}_{grant.version}"
        assert await ClientInvitations(settings, session).editor(42, selector) == cid
        with pytest.raises(DomainError):
            await ClientInvitations(settings, session).editor(43, selector)
        for actor in (42, 43):
            with pytest.raises(DomainError):
                await ClientInvitations(settings, session).consume(actor, payload)
        assert payload[3:] not in str(
            [r.details for r in await session.scalars(select(AuditEvent))]
        )


async def test_invitation_for_assigned_client_rejects_other_user_without_consuming(
    database, settings
):
    cid = await create(database, settings)
    await assign(database, settings, cid)
    payload = await invite(database, settings, cid)
    async with database() as session:
        links = ClientInvitations(settings, session)
        with pytest.raises(DomainError, match="ac_denied"):
            await links.consume(43, payload)
        assert await links.consume(42, payload) == cid


@pytest.mark.parametrize("mode", ["expired", "rotated", "revoked", "replaced", "malformed"])
async def test_invalid_invitation_states_never_grant_access(database, settings, monkeypatch, mode):
    cid = await create(database, settings)
    payload = await invite(database, settings, cid)
    if mode == "rotated":
        await invite(database, settings, cid)
    elif mode == "expired":
        future = now() + timedelta(days=2)
        monkeypatch.setattr(invitation_module, "now", lambda: future)
    elif mode == "revoked":
        async with database() as session:
            await ClientInvitations(settings, session).revoke(900, cid)
    elif mode == "replaced":
        await assign(database, settings, cid, customer=43)
    else:
        payload = "ai_not-a-token"
    async with database() as session:
        with pytest.raises(DomainError):
            await ClientInvitations(settings, session).consume(42, payload)
        assert not await session.scalar(
            select(AdCustomerGrant.id).where(AdCustomerGrant.customer_id == 42)
        )


async def test_revoke_editor_link_and_replace_client_reject_stale_selectors_and_drafts(
    database, settings
):
    cid = await create(database, settings)
    grant = await assign(database, settings, cid)
    selector = f"ae_{cid}_{grant.id}_{grant.version}"
    async with database() as session:
        access = CustomerAccess(settings, session)
        await access.unlink(900, cid, access.stamp(await access.latest(cid)))
        with pytest.raises(DomainError):
            await ClientInvitations(settings, session).editor(42, selector)
        assert (await CustomerAdsService(settings, session).current(42, cid))[0].id == cid
    await assign(database, settings, cid, 43)
    async with database() as session:
        with pytest.raises(DomainError):
            await CustomerAdsService(settings, session).current(42, cid)


async def test_customer_save_next_slot_atomic_version_and_cross_ad_denial(database, settings):
    cid = await create(database, settings)
    other = await create(database, settings, "Other", "other")
    await groups(database)
    await assign(database, settings, cid)
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, cid)
        await service.groups(900, cid, policy.version, [-100], Bot())
        policy = await service.policy(900, cid)
        await service.lifecycle(900, cid, policy.version, "START", Bot())
        before = utc((await service.schedules(cid))[0].next_run_at)
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42, cid, 2, "TEXT", {"text": "New client text", "entities": []}, "client-edit"
        )
        await ads.mark_previewed(42, draft.id)
        await ads.save(42, draft.id)
        assert utc((await service.schedules(cid))[0].next_run_at) == before
        assert await session.scalar(select(func.count()).select_from(AdPublicationAttempt)) == 0
        assert (await ads.current(42, cid))[1].content["text"] == "New client text"
        with pytest.raises(DomainError):
            await ads.stage(42, other, 2, "TEXT", {"text": "Attack", "entities": []}, "foreign")
        assert (await AdsService(settings, session).current(900, other))[1].content[
            "text"
        ] == "Other"


async def test_settings_stats_and_audit_are_ad_scoped_and_stale_buttons_fail(database, settings):
    a = await create(database, settings)
    b = await create(database, settings, "B", "b")
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, a)
        expected = policy.version
        await service.change(900, a, expected, "interval_seconds", 7200, Bot())
        with pytest.raises(DomainError, match="conflict"):
            await service.change(900, a, expected, "interval_seconds", 86400, Bot())
        assert all(e.details["campaign_id"] == a for e in await service.history(900, a))
        assert not any(e.action == "ads_settings_changed" for e in await service.history(900, b))
        assert (await service.statistics(900, b))["total"] == 0
        with pytest.raises(DomainError):
            await service.change(42, a, expected + 1, "interval_seconds", 3600, Bot())


@pytest.mark.parametrize("kind", ["animation", "document"])
async def test_additional_media_validation_and_real_sender_method_contract(kind):
    content = validate_content(
        {
            **deepcopy(EMPTY),
            "text": "Caption",
            "media": [{"kind": kind, "file_id": "telegram_file"}],
        }
    )
    assert parts_for(content) == [kind.upper()]
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=42, type="private"),
        **{
            kind: {
                "file_id": "telegram_file",
                "file_unique_id": "unique",
                **({"width": 10, "height": 10, "duration": 1} if kind == "animation" else {}),
            }
        },
    )
    assert media_from_message(message) == content["media"][0]
    calls = []

    async def send(**kwargs):
        calls.append(kwargs)
        return Message(message_id=1, date=now(), chat=Chat(id=-100, type="supergroup"))

    bot = SimpleNamespace(**{"send_" + kind: send})
    assert await send_part(
        bot,
        SimpleNamespace(id=1, chat_id=-100),
        SimpleNamespace(kind=kind.upper()),
        SimpleNamespace(content=content),
    ) == [1]
    assert calls[0][kind] == "telegram_file" and calls[0]["caption"] == "Caption"
    with pytest.raises(DomainError):
        validate_content({**content, "media": content["media"] * 2})


def test_html_converts_tags_to_utf16_entities_and_rejects_unsafe_urls():
    message = Message(
        message_id=1,
        date=now(),
        chat=Chat(id=42, type="private"),
        text="<b>😀 Hello</b> <u>there</u>",
    )
    parsed = text_from_message(message)
    assert parsed["text"] == "😀 Hello there"
    assert parsed["entities"][0] == {"type": "bold", "offset": 0, "length": 8}
    validate_content({**deepcopy(EMPTY), **parsed})
    with pytest.raises(DomainError):
        text_from_message(
            message.model_copy(update={"text": '<a href="javascript:alert(1)">x</a>'})
        )
    # Native entity input takes precedence: literal HTML remains literal when explicitly formatted.
    native = text_from_message(
        message.model_copy(
            update={
                "text": "<b>x</b>",
                "entities": [MessageEntity(type="bold", offset=0, length=8)],
            }
        )
    )
    assert native["text"] == "<b>x</b>"


async def invitation_race(sessions, settings):
    cid = await create(sessions, settings)
    payload = await invite(sessions, settings, cid)

    async def redeem(actor):
        async with sessions() as session:
            try:
                await ClientInvitations(settings, session).consume(actor, payload)
                return actor
            except DomainError:
                return None

    results = await asyncio.gather(redeem(42), redeem(43))
    assert len([r for r in results if r]) == 1
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AdCustomerGrant)) == 1


async def test_sqlite_invitation_parallel_consumption(database, settings):
    await invitation_race(database, settings)


async def test_postgres_invitation_parallel_consumption(postgres_contract):
    sessions, settings = postgres_contract
    await invitation_race(sessions, settings)


async def test_issuer_admin_revocation_prevents_redeeming_old_invitation(database, settings):
    from app.models import Administrator

    cid = await create(database, settings)
    async with database() as session:
        admin = Administrator(telegram_id=901, active=True, assigned_by=900)
        session.add(admin)
        await session.commit()
        payload = await ClientInvitations(settings, session).issue(901, cid, "0.0")
        admin.active = False
        await session.commit()
        with pytest.raises(DomainError):
            await ClientInvitations(settings, session).consume(42, payload)


@pytest.mark.parametrize("status", ["PAUSED", "EXPIRED"])
async def test_customer_edits_never_reactivate_paused_or_expired_ad(database, settings, status):
    cid = await create(database, settings)
    await groups(database)
    await assign(database, settings, cid)
    async with database() as session:
        session.add(
            AdSchedule(
                campaign_id=cid,
                chat_id=-100,
                status=status,
                authorized_by=900,
                interval_seconds=3600,
            )
        )
        await session.commit()
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42, cid, 2, "TEXT", {"text": "Updated", "entities": []}, "paused-update"
        )
        await ads.mark_previewed(42, draft.id)
        await ads.save(42, draft.id)
        row = await session.scalar(select(AdSchedule).where(AdSchedule.campaign_id == cid))
        assert row.status == status and row.next_run_at is None


async def settings_race(sessions, settings):
    cid = await create(sessions, settings)
    async with sessions() as session:
        expected = (await UnifiedAds(settings, session).policy(900, cid)).version

    async def change(seconds):
        async with sessions() as session:
            try:
                await UnifiedAds(settings, session).change(
                    900, cid, expected, "interval_seconds", seconds, Bot()
                )
                return "ok"
            except DomainError as error:
                return error.code

    assert sorted(await asyncio.gather(change(7200), change(86400))) == ["ads_conflict", "ok"]


async def test_postgres_parallel_admin_settings_are_versioned(postgres_contract):
    sessions, settings = postgres_contract
    await settings_race(sessions, settings)


async def test_process_restart_retains_ads_grants_schedule_and_history(tmp_path, settings):
    from app.db import create_database
    from app.models import Base

    url = f"sqlite+aiosqlite:///{tmp_path}/restart.db"
    engine, sessions = create_database(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    cid = await create(sessions, settings)
    await assign(sessions, settings, cid)
    await groups(sessions)
    async with sessions() as session:
        service = UnifiedAds(settings, session)
        p = await service.policy(900, cid)
        await service.groups(900, cid, p.version, [-100], Bot())
    await engine.dispose()
    engine, sessions = create_database(url)
    try:
        async with sessions() as session:
            service = UnifiedAds(settings, session)
            assert (await service.ads.current(900, cid))[1].content["text"] == "REDSAFE PARTNER"
            assert (await service.grants.check(42, cid)).customer_id == 42
            assert (await service.schedules(cid))[0].chat_id == -100
            assert await service.history(900, cid)
    finally:
        await engine.dispose()


async def test_customer_edit_preserves_pinned_attempt_and_next_slot_uses_new_revision(
    database, settings, clock
):
    from app.bot.ads_publisher import AdsPublisher

    cid, sid, bot, _, _ = await configured(database, settings, clock)
    await assign(database, settings, cid)
    async with database() as session:
        pub = PublicationService(settings, session)
        assert await pub.enqueue() == 1
        first = await session.scalar(select(AdPublicationAttempt))
        pinned = first.content_version_id
        next_due = (await session.get(AdSchedule, sid)).next_run_at
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42, cid, 2, "TEXT", {"text": "New version", "entities": []}, "new-client-version"
        )
        await ads.mark_previewed(42, draft.id)
        revision = await ads.save(42, draft.id)
        assert revision.id != pinned
        assert (await session.get(AdSchedule, sid)).next_run_at == next_due
        assert (await session.get(AdPublicationAttempt, first.id)).content_version_id == pinned
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    assert bot.calls[0][1]["text"].startswith("Old ")
    clock[0] += timedelta(minutes=30)
    await worker.tick()
    assert bot.calls[-1][1]["text"] == "New version"


async def test_clear_audience_stops_only_that_ad_and_keeps_statistics(database, settings):
    cid = await create(database, settings)
    await groups(database)
    async with database() as session:
        service = UnifiedAds(settings, session)
        policy = await service.policy(900, cid)
        await service.groups(900, cid, policy.version, [-100], Bot())
        policy = await service.policy(900, cid)
        await service.groups(900, cid, policy.version, [], Bot())
        rows = await service.schedules(cid)
        assert len(rows) == 1 and rows[0].excluded and rows[0].status == "STOPPED"
        assert (await service.ads.current(900, cid))[1].content["text"] == "REDSAFE PARTNER"

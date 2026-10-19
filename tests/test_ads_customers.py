"""Customer capabilities tested against actual isolated SQL transactions."""

import asyncio
import hashlib
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_integration_contracts import postgres_contract as postgres_contract

from app.ads_content import parse_buttons
from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_publication import PublicationService
from app.ads_services import AdsService
from app.db import create_database
from app.errors import DomainError
from app.models import (
    AdCampaign,
    AdContentDraft,
    AdContentVersion,
    AdCustomerDraft,
    AdCustomerGrant,
    AdCustomerLink,
    AuditEvent,
    Base,
    now,
)


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def database(request, tmp_path):
    if request.param == "postgres":
        fixture = postgres_contract.__wrapped__()
        sessions, _ = await anext(fixture)
        try:
            yield sessions
        finally:
            await fixture.aclose()
    else:
        engine, sessions = create_database(f"sqlite+aiosqlite:///{tmp_path}/customer.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            yield sessions
        finally:
            await engine.dispose()


async def setup(sessions, settings, customer=42):
    async with sessions() as session:
        campaign = await AdsService(settings, session).create(900, "ADMIN_INTERNAL_ONLY", "seed")
        grant = await CustomerAccess(settings, session).assign(900, campaign.id, customer, "0.0")
        cid, gid = campaign.id, grant.id
    return cid, gid


async def stamp(access, cid):
    return access.stamp(await access.latest(cid))


async def link(sessions, settings, cid):
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        return await access.link(900, cid, await stamp(access, cid))


async def pending(sessions, settings, cid, request="edit"):
    async with sessions() as session:
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42, cid, 1, "TEXT", {"text": "Ad <b>literal</b> 😀", "entities": []}, request
        )
        did = draft.id
        await ads.mark_previewed(42, did)
        return did


@pytest.mark.parametrize("actor", [42, 43, 900])
async def test_link_numeric_authorization_and_hash_only(database, settings, actor):
    cid, gid = await setup(database, settings)
    token = await link(database, settings, cid)
    assert len("ad_" + token) <= 64
    async with database() as session:
        access = CustomerAccess(settings, session)
        row = await session.scalar(select(AdCustomerLink))
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert row.grant_id == gid
        if actor == 42:
            assert await access.resolve(actor, token) == cid
        else:
            with pytest.raises(DomainError, match="ac_denied"):
                await access.resolve(actor, token)
        logs = list(await session.scalars(select(AuditEvent)))
        assert token not in repr([(r.action, r.details) for r in logs])
        assert not hasattr(row, "token")


@pytest.mark.parametrize("operation", ["revoke", "rotate", "replace", "same_customer", "archive"])
async def test_old_link_and_draft_never_revive(database, settings, operation):
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    did = await pending(database, settings, cid)
    async with database() as session:
        access = CustomerAccess(settings, session)
        expected = await stamp(access, cid)
        if operation == "revoke":
            await access.revoke(900, cid, expected)
        elif operation == "rotate":
            new = await access.link(900, cid, expected, rotate=True)
            assert await access.resolve(42, new) == cid
        elif operation in {"replace", "same_customer"}:
            await access.assign(900, cid, 43 if operation == "replace" else 42, expected)
        else:
            await AdsService(settings, session).archive(900, cid, 1)
    async with database() as session:  # Restart: fresh identity map and service.
        with pytest.raises(DomainError):
            await CustomerAccess(settings, session).resolve(42, token)
        if operation != "rotate":
            with pytest.raises(DomainError):
                await CustomerAdsService(settings, session).save(42, did)
            assert (await session.get(AdCampaign, cid)).version == 1
        else:  # Revoking a link is distinct from revoking the numeric grant.
            assert (await CustomerAdsService(settings, session).save(42, did)).revision == 2


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "TEXT",
            {
                "text": "😀 <b>native</b>",
                "entities": [
                    {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "12345"}
                ],
            },
        ),
        (
            "MEDIA",
            [{"kind": "photo", "file_id": "photo_1"}, {"kind": "video", "file_id": "video_1"}],
        ),
        (
            "BUTTONS",
            parse_buttons(
                "😀 Open - https://t.me/Example && Copy - copy: Hi\nPopup - popup: Hi\nAlert - alert: Hello\nShare - share: Hello",
                [{"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "98765"}],
            ),
        ),
    ],
)
async def test_shared_editor_auto_approves_immutable_versions(database, settings, field, value):
    cid, gid = await setup(database, settings)
    async with database() as session:
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(42, cid, 1, field, value, "content")
        did = draft.id
        binding = await session.get(AdCustomerDraft, did)
        assert (binding.grant_id, binding.customer_id, binding.campaign_id) == (gid, 42, cid)
        with pytest.raises(DomainError, match="ads_preview_required"):
            await ads.save(42, did)
        await ads.mark_previewed(42, did)
        saved = await ads.save(42, did)
        assert saved.revision == 2 and saved.actor_id == 42
        assert (await ads.save(42, did)).id == saved.id
        assert (await ads.current(42, cid))[1].content == saved.content
        assert len(await ads.history(42, cid)) == 2
        assert saved.content[{"TEXT": "text", "MEDIA": "media", "BUTTONS": "buttons"}[field]] == (
            value["text"] if field == "TEXT" else value
        )
        assert (
            await session.scalar(select(AdContentVersion).where(AdContentVersion.revision == 1))
        ).content["text"] == ""


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("RESTORE", 1, "ac_denied"),
        ("NAME", "Renamed", "ac_denied"),
        (
            "TEXT",
            {"text": "x", "entities": [{"type": "bold", "offset": 10, "length": 3}]},
            "ads_entity",
        ),
        ("MEDIA", [{"kind": "document", "file_id": "bad"}], "ads_content"),
        (
            "BUTTONS",
            [
                [
                    {
                        "label": "Bad",
                        "kind": "url",
                        "value": "javascript:alert(1)",
                        "icon_custom_emoji_id": None,
                    }
                ]
            ],
            "ads_url",
        ),
    ],
)
async def test_invalid_content_and_admin_only_fields_blocked(
    database, settings, field, value, code
):
    cid, _ = await setup(database, settings)
    async with database() as session:
        with pytest.raises(DomainError, match=code):
            await CustomerAdsService(settings, session).stage(42, cid, 1, field, value, "invalid")
        assert (await session.get(AdCampaign, cid)).version == 1
        assert await session.scalar(select(func.count()).select_from(AdContentDraft)) == 0


async def test_no_admin_capability_and_no_other_campaign_access(database, settings):
    cid, _ = await setup(database, settings)
    async with database() as session:
        ads = CustomerAdsService(settings, session)
        other = await AdsService(settings, session).create(900, "OTHER", "other")
        for operation in [
            ads.get(42, other.id),
            ads.history(43, cid),
            ads.create(42, "X", "x"),
            ads.archive(42, cid, 1),
            CustomerAccess(settings, session).assign(42, cid, 42, "0.0"),
            PublicationService(settings, session).one(42, cid),
        ]:
            with pytest.raises(DomainError):
                await operation
        assert [c.id for c in await ads.campaigns(42)] == [cid]
        assert await ads.campaigns(43) == []
        assert (await session.get(AdCampaign, cid)).internal_name == "ADMIN_INTERNAL_ONLY"


async def test_expiry_is_read_only_and_never_publication_configuration(database, settings):
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    did = await pending(database, settings, cid)
    async with database() as session:
        access = CustomerAccess(settings, session)
        await access.deadline(900, cid, await stamp(access, cid), now() - timedelta(seconds=1))
    async with database() as session:
        ads = CustomerAdsService(settings, session)
        assert await ads.grants.resolve(42, token) == cid
        assert len(await ads.history(42, cid)) == 1
        with pytest.raises(DomainError, match="ac_readonly"):
            await ads.save(42, did)
        assert (await session.get(AdCampaign, cid)).version == 1


async def test_stale_admin_confirmation_and_duplicate_token_creation(database, settings):
    cid, _ = await setup(database, settings)
    async with database() as session:
        access = CustomerAccess(settings, session)
        expected = await stamp(access, cid)
        await access.link(900, cid, expected)
        for op in [
            access.link(900, cid, expected),
            access.revoke(900, cid, expected),
            access.assign(900, cid, 43, expected),
        ]:
            with pytest.raises(DomainError, match="ads_conflict"):
                await op
        with pytest.raises(DomainError, match="ac_once"):
            await access.link(900, cid, await stamp(access, cid))
        assert await session.scalar(select(func.count()).select_from(AdCustomerLink)) == 1


async def test_old_fsm_grant_cannot_stage_under_new_assignment(database, settings):
    cid, gid = await setup(database, settings)
    async with database() as session:
        access = CustomerAccess(settings, session)
        await access.assign(900, cid, 42, await stamp(access, cid))
        ads = CustomerAdsService(settings, session)
        ads.expected_grant = gid
        with pytest.raises(DomainError, match="ac_denied"):
            await ads.stage(42, cid, 1, "TEXT", {"text": "Old FSM", "entities": []}, "old-fsm")
        assert await session.scalar(select(func.count()).select_from(AdContentDraft)) == 0


async def test_admin_restore_still_creates_new_revision(database, settings):
    cid, _ = await setup(database, settings)
    did = await pending(database, settings, cid)
    async with database() as session:
        original = (await AdsService(settings, session).current(900, cid))[1]
        await CustomerAdsService(settings, session).save(42, did)
        admin = AdsService(settings, session)
        restored = await admin.stage(900, cid, 2, "RESTORE", original.id, "restore")
        await admin.mark_previewed(900, restored.id)
        version = await admin.save(900, restored.id)
        assert (
            version.revision == 3
            and version.actor_id == 900
            and version.content == original.content
        )
        assert [v.actor_id for v in await admin.history(900, cid)] == [900, 42, 900]


async def test_real_postgres_concurrent_saves_revoke_and_rollback(postgres_contract):
    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    first = await pending(sessions, settings, cid, "first")
    second = await pending(sessions, settings, cid, "second")

    async def save(did):
        async with sessions() as session:
            try:
                return (await CustomerAdsService(settings, session).save(42, did)).revision
            except DomainError as error:
                return error.code

    assert sorted(
        await asyncio.wait_for(asyncio.gather(save(first), save(second)), 10), key=str
    ) == [2, "ads_conflict"]
    async with sessions() as session:
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(
            42, cid, 2, "TEXT", {"text": "Never committed", "entities": []}, "third"
        )
        did = draft.id
        await ads.mark_previewed(42, did)
    # Hold the actual ACL boundary, commit revocation, and ensure a blocked save reloads authority.
    async with sessions() as owner:
        access = CustomerAccess(settings, owner)
        async with access.ads.write(900):
            grant = await access.latest(cid)
            grant.status, grant.revoked_by, grant.revoked_at = "REVOKED", 900, now()
            task = asyncio.create_task(save(did))
            await asyncio.sleep(0.05)
            assert not task.done()
        assert await asyncio.wait_for(task, 10) == "ac_denied"
    async with sessions() as session:
        assert (await session.get(AdCampaign, cid)).version == 2
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 2
        assert (await session.get(AdContentDraft, did)).status == "READY"


async def test_real_postgres_grant_uniqueness_and_foreign_binding(postgres_contract):
    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    async with sessions() as session:
        session.add(AdCustomerGrant(campaign_id=cid, customer_id=43, assigned_by=900))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(AdCustomerGrant)) == 1
        draft = await AdsService(settings, session).stage(
            900, cid, 1, "TEXT", {"text": "Admin", "entities": []}, "admin"
        )
        grant = await CustomerAccess(settings, session).latest(cid)
        session.add(
            AdCustomerDraft(draft_id=draft.id, campaign_id=cid, customer_id=43, grant_id=grant.id)
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
        assert await session.scalar(select(func.count()).select_from(AdCustomerDraft)) == 0


async def test_real_postgres_concurrent_assign_and_link_rotation(postgres_contract):
    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    async with sessions() as session:
        expected = await stamp(CustomerAccess(settings, session), cid)

    async def assign(customer):
        async with sessions() as session:
            try:
                await CustomerAccess(settings, session).assign(900, cid, customer, expected)
                return "ok"
            except DomainError as error:
                return error.code

    assert sorted(await asyncio.wait_for(asyncio.gather(assign(43), assign(44)), 10)) == [
        "ads_conflict",
        "ok",
    ]
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AdCustomerGrant)
                .where(AdCustomerGrant.status == "ACTIVE")
            )
            == 1
        )
        expected = await stamp(access, cid)

    async def rotate():
        async with sessions() as session:
            try:
                return await CustomerAccess(settings, session).link(900, cid, expected, rotate=True)
            except DomainError as error:
                return error.code

    result = await asyncio.wait_for(asyncio.gather(rotate(), rotate()), 10)
    assert len([r for r in result if len(r) == 43]) == 1 and result.count("ads_conflict") == 1
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AdCustomerLink)
                .where(AdCustomerLink.status == "ACTIVE")
            )
            == 1
        )


async def test_real_postgres_revoke_before_preview_and_atomic_rollback(
    postgres_contract, monkeypatch
):
    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    async with sessions() as session:
        ads = CustomerAdsService(settings, session)
        draft = await ads.stage(42, cid, 1, "TEXT", {"text": "Preview", "entities": []}, "preview")
        did = draft.id
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        await access.revoke(900, cid, await stamp(access, cid))
    async with sessions() as session:
        with pytest.raises(DomainError):
            await CustomerAdsService(settings, session).mark_previewed(42, did)
        assert not (await session.get(AdContentDraft, did)).previewed
        access = CustomerAccess(settings, session)
        grant = await access.assign(900, cid, 42, await stamp(access, cid))
        expected = access.stamp(grant)

        def abort(*args, **kwargs):
            raise RuntimeError("isolated failure")

        monkeypatch.setattr(access.ads, "audit", abort)
        with pytest.raises(RuntimeError):
            await access.assign(900, cid, 43, expected)
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        current = await access.check(42, cid)
        assert access.stamp(current) == expected and current.status == "ACTIVE"
        assert await session.scalar(select(func.count()).select_from(AdCustomerGrant)) == 2


async def test_customer_save_preserves_active_schedules_and_pinned_publication(postgres_contract):
    from unittest.mock import patch

    from test_ads_publication import INSTANT, configured

    import app.ads_publication as publication
    from app.models import AdPublicationAttempt, AdSchedule

    sessions, settings = postgres_contract
    with patch.object(publication, "now", return_value=INSTANT):
        cid, sid, bot, _, revision = await configured(sessions, settings, INSTANT)
        async with sessions() as session:
            access = CustomerAccess(settings, session)
            await access.assign(900, cid, 42, "0.0")
            schedule = await session.get(AdSchedule, sid)
            planner = PublicationService(settings, session)
            await planner.enqueue()
            attempt = await session.scalar(select(AdPublicationAttempt))
            assert attempt.content_version_id == revision
            before = {
                column.name: getattr(schedule, column.name)
                for column in AdSchedule.__table__.columns
            }
            aid = attempt.id
            ads = CustomerAdsService(settings, session)
            base = (await ads.get(42, cid)).version
            draft = await ads.stage(
                42,
                cid,
                base,
                "TEXT",
                {"text": "Customer next publication", "entities": []},
                "customer-live-sql",
            )
            await ads.mark_previewed(42, draft.id)
            await ads.save(42, draft.id)
        async with sessions() as session:
            schedule = await session.get(AdSchedule, sid)
            assert {
                column.name: getattr(schedule, column.name)
                for column in AdSchedule.__table__.columns
            } == before
            assert (await session.get(AdPublicationAttempt, aid)).content_version_id == revision


async def test_archived_customer_access_can_still_be_revoked(database, settings):
    cid, _ = await setup(database, settings)
    async with database() as session:
        access = CustomerAccess(settings, session)
        await AdsService(settings, session).archive(900, cid, 1)
        assert (await CustomerAdsService(settings, session).get(42, cid)).status == "ARCHIVED"
        await access.revoke(900, cid, await stamp(access, cid))
        with pytest.raises(DomainError, match="ac_denied"):
            await CustomerAdsService(settings, session).history(42, cid)


async def test_admin_and_customer_draft_scopes_do_not_mix(database, settings):
    cid, _ = await setup(database, settings, customer=900)
    async with database() as session:
        admin, customer = AdsService(settings, session), CustomerAdsService(settings, session)
        draft = await admin.stage(
            900, cid, 1, "TEXT", {"text": "Admin", "entities": []}, "admin-scope"
        )
        admin_id = draft.id
        draft = await customer.stage(
            900, cid, 1, "TEXT", {"text": "Customer", "entities": []}, "customer-scope"
        )
        customer_id = draft.id
        with pytest.raises(DomainError):
            await customer.save(900, admin_id)
        with pytest.raises(DomainError):
            await admin.save(900, customer_id)
        assert [d.id for d in await admin.unfinished(900)] == [admin_id]
        assert [d.id for d in await customer.unfinished(900)] == [customer_id]


@pytest.mark.parametrize("token", ["", "x", "x" * 100000, "<script>", "a" * 43, "😀" * 43])
async def test_malformed_and_unknown_tokens_have_uniform_rejection(database, settings, token):
    await setup(database, settings)
    async with database() as session:
        with pytest.raises(DomainError, match="^ac_denied$"):
            await CustomerAccess(settings, session).resolve(42, token)


async def test_unlink_does_not_revoke_numeric_access(database, settings):
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    did = await pending(database, settings, cid)
    async with database() as session:
        access = CustomerAccess(settings, session)
        await access.unlink(900, cid, await stamp(access, cid))
        with pytest.raises(DomainError, match="ac_denied"):
            await access.resolve(42, token)
        assert (await CustomerAdsService(settings, session).save(42, did)).revision == 2


@pytest.mark.parametrize(
    "field,value", [("interval_seconds", 3600), ("chat_id", -999), ("end_at", None)]
)
async def test_customer_cannot_write_publication_fields(database, settings, field, value):
    cid, _ = await setup(database, settings)
    async with database() as session:
        with pytest.raises(DomainError):
            await PublicationService(settings, session).change(
                42, cid, 1, field, value, "forge", None
            )
        assert (await session.get(AdCampaign, cid)).version == 1


@pytest.mark.parametrize("kind", ["grant", "link"])
async def test_real_postgres_revoke_metadata_constraints(postgres_contract, kind):
    from sqlalchemy import update

    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    if kind == "link":
        await link(sessions, settings, cid)
    model = AdCustomerGrant if kind == "grant" else AdCustomerLink
    async with sessions() as session:
        with pytest.raises(IntegrityError):
            await session.execute(
                update(model).values(status="REVOKED", revoked_at=now(), revoked_by=None)
            )
            await session.commit()
        await session.rollback()
        assert await session.scalar(select(model.status)) == "ACTIVE"


async def test_real_postgres_latest_grant_lookup_uses_history_index(postgres_contract):
    from sqlalchemy import text

    sessions, settings = postgres_contract
    cid, _ = await setup(sessions, settings)
    async with sessions() as session:
        access = CustomerAccess(settings, session)
        await access.revoke(900, cid, await stamp(access, cid))
        campaign = await AdsService(settings, session).create(
            900, "Unrelated history", "unrelated-history"
        )
        session.add_all(
            [
                AdCustomerGrant(
                    campaign_id=campaign.id,
                    customer_id=1000 + i,
                    assigned_by=900,
                    status="REVOKED",
                    revoked_at=now(),
                    revoked_by=900,
                )
                for i in range(1000)
            ]
        )
        await session.commit()
        await session.execute(text("ANALYZE ad_customer_grants"))
        plan = await session.scalar(
            text(
                "EXPLAIN (ANALYZE, FORMAT JSON) SELECT * FROM ad_customer_grants WHERE campaign_id = :cid ORDER BY id DESC LIMIT 1"
            ),
            {"cid": cid},
        )
        assert "ix_ad_grant_campaign" in repr(plan)
        node = plan[0]["Plan"]
        assert node["Actual Rows"] == 1 and node["Actual Loops"] == 1
        assert (await access.latest(cid)).status == "REVOKED"

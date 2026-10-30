"""Direct administrator transactions, preservation, authorization and concurrency."""

import asyncio

import pytest
from sqlalchemy import func, select
from test_integration_contracts import postgres_contract as postgres_contract

from app.errors import DomainError
from app.merchant_services import MerchantService
from app.models import (
    AuditEvent,
    MerchantEntry,
    MerchantProposal,
    MerchantSettings,
    ScamRecord,
    TrustedDesignation,
)
from app.services import Service


async def setup(session, settings):
    core = Service(settings, session)
    await core.observe(42, "seller42", "Seller")
    await core.observe(43, "seller43", "Other")
    await session.commit()
    return MerchantService(core)


def fields(**changes):
    return {
        "name": "North Studio",
        "city_code": "vilnius",
        "description": "Legal professional services",
        **changes,
    }


async def created(cat, target=42):
    return await cat.save(900, 0, 0, fields(), target_id=target)


async def test_direct_creation_is_durable_public_without_application_or_trust(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        entry = await created(cat)
        eid = entry.id
        assert entry.status == "APPROVED"
        assert (await cat.listing())[1] == 1
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0
        assert await s.scalar(select(func.count()).select_from(TrustedDesignation)) == 0
    async with database() as s:
        assert (await MerchantService(Service(settings, s)).public(eid)).name == "North Studio"


@pytest.mark.parametrize("operation", ["create", "read", "edit", "status", "settings", "target"])
async def test_non_admin_cannot_manage_even_own_entry(database, settings, operation):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        eid, version = e.id, e.version
        with pytest.raises(DomainError, match="forbidden"):
            if operation == "create":
                await cat.save(42, 0, 0, fields(), target_id=43)
            elif operation == "read":
                await cat.editable(42, eid)
            elif operation == "edit":
                await cat.save(42, eid, version, {"name": "Hijack"})
            elif operation == "status":
                await cat.change_status(42, eid, version, "REMOVED")
            elif operation == "target":
                await cat.creation_target(42, 43)
            else:
                await cat.configure(42, 1, "enabled", False)
        await s.rollback()
        assert (await cat.public(eid)).name == "North Studio"


async def test_single_field_edit_keeps_identity_content_and_other_listing(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        a = await created(cat)
        b = await created(cat, 43)
        bid, bversion = b.id, b.version
        owner_id = a.owner_user_id
        a = await cat.save(900, a.id, a.version, {"name": "Baltic Works"})
        assert a.description == "Legal professional services" and a.city_code == "vilnius"
        assert a.owner_user_id == owner_id and a.owner.telegram_id == 42
        b = await cat.repo.entry(bid)
        assert b.name == "North Studio" and b.version == bversion
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 2
        assert (await cat.listing(query="north"))[1] == 1
        assert (await cat.listing(query="tic wor"))[1] == 1
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0


async def test_city_contact_paragraphs_edit_do_not_reassign_identity_or_username(
    database, settings
):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        text = "Pirma <eilutė> & tekstas\n\nAntra pastraipa."
        e = await cat.save(
            900,
            e.id,
            e.version,
            {"description": text, "city_code": "kaunas", "contact": "@other_contact"},
        )
        assert e.description == text and e.contact == "@other_contact"
        assert e.owner.username == "seller42" and e.owner.telegram_id == 42
        assert (await cat.listing(city_code="kaunas"))[1] == 1
        assert (await cat.listing(city_code="vilnius"))[1] == 0
        e = await cat.save(900, e.id, e.version, {"contact": "-"})
        assert e.contact is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "x"),
        ("description", "short"),
        ("city_code", "invalid"),
        ("contact", "javascript:alert(1)"),
        ("contact", "https://host:bad/path"),
        ("contact", "https://u:p@host/"),
        ("owner_user_id", 2),
    ],
)
async def test_invalid_field_has_no_partial_write(database, settings, field, value):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        eid, version = e.id, e.version
        with pytest.raises(DomainError, match="merchant_invalid"):
            await cat.save(900, eid, version, {"name": "Changed name", field: value})
        await s.rollback()
        e = await cat.public(eid)
        assert e.name == "North Studio" and e.version == version


async def test_stale_save_and_duplicate_creation_never_add_another_entry(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        eid, version = e.id, e.version
        await cat.save(900, eid, version, {"name": "Current Name"})
        with pytest.raises(DomainError, match="merchant_stale"):
            await cat.save(900, eid, version, {"name": "Stale Name"})
        await s.rollback()
        with pytest.raises(DomainError, match="merchant_duplicate"):
            await created(cat)
        await s.rollback()
        assert (await cat.public(eid)).name == "Current Name"
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 1


async def test_suspend_edit_resume_remove_preserve_data_and_audit(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        eid, owner = e.id, e.owner_user_id
        e = await cat.change_status(900, eid, e.version, "SUSPENDED")
        assert (await cat.listing())[1] == 0
        e = await cat.save(900, eid, e.version, {"name": "Changed while paused"})
        assert e.status == "SUSPENDED" and (await cat.listing())[1] == 0
        e = await cat.change_status(900, eid, e.version, "APPROVED")
        assert (await cat.listing())[1] == 1
        e = await cat.change_status(900, eid, e.version, "REMOVED")
        assert (await cat.listing())[1] == 0 and e.name == "Changed while paused"
        assert e.owner_user_id == owner
        assert await cat.core.repo.user_by_telegram(42) is not None
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "merchant_status")
            )
            == 3
        )


async def test_scam_blocks_resume_and_creation_without_modifying_scam_or_trust(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        e = await cat.change_status(900, e.id, e.version, "SUSPENDED")
        eid, version = e.id, e.version
        s.add(ScamRecord(target_id=e.owner_user_id, reason="Confirmed fraud", moderator_id=900))
        await s.commit()
        with pytest.raises(DomainError, match="merchant_blocked"):
            await cat.change_status(900, eid, version, "APPROVED")
        await s.rollback()
        assert (await cat.repo.entry(eid)).status == "SUSPENDED"
        assert await s.scalar(select(func.count()).select_from(ScamRecord)) == 1
        assert await s.scalar(select(func.count()).select_from(TrustedDesignation)) == 0


async def test_settings_durable_and_registration_setting_retired(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        row = await cat.configure(900, 1, "publish_authors", True)
        await cat.configure(900, row.version, "title", "Custom directory", lang="en")
        with pytest.raises(DomainError, match="merchant_invalid"):
            await cat.configure(900, row.version, "registrations", True)
        await s.rollback()
    async with database() as s:
        row = await s.get(MerchantSettings, 1)
        assert row.publish_authors and row.copy["en"]["title"] == "Custom directory"


@pytest.mark.parametrize("operation", ["create", "edit"])
async def test_postgres_concurrent_admin_writes_one_winner(postgres_contract, settings, operation):
    sessions, _ = postgres_contract
    async with sessions() as s:
        cat = await setup(s, settings)
        e = await created(cat) if operation == "edit" else None
        eid, version = (e.id, e.version) if e else (0, 0)

    async def write(label):
        async with sessions() as s:
            cat = MerchantService(Service(settings, s))
            try:
                return (await cat.save(900, eid, version, fields(name=label), target_id=42)).id
            except DomainError as error:
                await s.rollback()
                return error.code

    outcomes = await asyncio.gather(write("First Studio"), write("Second Studio"))
    assert sum(isinstance(x, int) for x in outcomes) == 1
    assert ("merchant_duplicate" if operation == "create" else "merchant_stale") in outcomes
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantEntry)) == 1
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 0


async def test_historical_requests_survive_direct_edit_unchanged(database, settings):
    from datetime import timedelta

    from app.models import now

    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        old = MerchantProposal(
            entry_id=e.id,
            actor_id=42,
            kind="EDIT",
            status="PENDING",
            name="Historical pending name",
            base_version=e.version,
            version=3,
            request_key="historical-request",
            expires_at=now() + timedelta(hours=1),
        )
        s.add(old)
        await s.commit()
        pid = old.id
        e = await cat.save(900, e.id, e.version, {"name": "Direct new name"})
        old = await s.get(MerchantProposal, pid)
        assert old.status == "PENDING" and old.version == 3
        assert old.name == "Historical pending name" and e.name == "Direct new name"
        assert not hasattr(cat, "submit") and not hasattr(cat, "moderate")


async def test_username_changes_preserve_merchant_owner_and_reputation_identity(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        eid, owner_id = e.id, e.owner_user_id
        await cat.core.observe(42, "renamed_seller", "Original owner")
        await cat.core.observe(43, "seller42", "Another account")
        await s.commit()
        assert (await cat.repo.mine(42)).id == eid
        assert (await cat.listing(query="seller42"))[1] == 0
        rows, total, _ = await cat.listing(query="RENAMED_SELLER")
        assert total == 1 and rows[0][0].owner_user_id == owner_id


async def test_edit_preserves_unchanged_historical_whitespace_and_approval_metadata(
    database, settings
):
    async with database() as s:
        cat = await setup(s, settings)
        e = await created(cat)
        description = "  Original paragraph\n\nLietuviškas aprašymas.  \n"
        e.description = description
        await s.commit()
        approved_at, approved_by = e.approved_at, e.approved_by
        snapshot = {
            field: getattr(e, field) for field in ("name", "city_code", "description", "contact")
        }
        snapshot["name"] = "Changed name only"
        e = await cat.save(900, e.id, e.version, snapshot)
        assert e.description == description
        assert e.approved_at == approved_at and e.approved_by == approved_by

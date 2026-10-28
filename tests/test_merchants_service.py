"""Phase C: durable proposals, atomic moderation, ownership and privacy boundaries."""

from datetime import timedelta

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
    now,
)
from app.services import Service


async def setup(session, settings):
    core = Service(settings, session)
    await core.observe(42, "seller42", "Seller")
    await core.observe(43, "seller43", "Other")
    await session.commit()
    return MerchantService(core)


async def submitted(cat, actor=42):
    p = await cat.begin(actor, consent=True)
    for field, value in (
        ("city_code", "vilnius"),
        ("name", "North Studio"),
        ("description", "Legal professional services"),
    ):
        p = await cat.update_draft(actor, p.id, field, value, p.version)
    return await cat.submit(actor, p.id, p.version)


async def test_registration_persists_and_only_approval_publishes(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        pid = p.id
        assert (await cat.listing())[1] == 0
    async with database() as s:
        cat = MerchantService(Service(settings, s))
        p = await s.get(MerchantProposal, pid)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        assert (await cat.public(entry.id)).name == "North Studio"
        assert await s.scalar(select(func.count()).select_from(TrustedDesignation)) == 0
        before = await s.scalar(select(func.count()).select_from(AuditEvent))
        await cat.moderate(900, p.id, p.version - 1, True)
        assert await s.scalar(select(func.count()).select_from(AuditEvent)) == before


async def test_proposal_owner_and_admin_permissions_are_server_checked(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        pid, version = p.id, p.version
        for op in ("read", "update", "approve"):
            with pytest.raises(DomainError) as e:
                if op == "read":
                    await cat.proposal(43, pid)
                elif op == "update":
                    await cat.update_draft(43, pid, "name", "Hijack", version)
                else:
                    await cat.moderate(43, pid, version, True)
            assert e.value.code == "forbidden"
            await s.rollback()
        assert (await s.get(MerchantProposal, pid)).status == "PENDING"


async def test_edit_waits_for_approval_and_rejection_preserves_public_data(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        p = await cat.begin(42, "EDIT", entry_id=entry.id)
        p = await cat.update_draft(42, p.id, "name", "Changed Studio", p.version)
        p = await cat.submit(42, p.id, p.version)
        assert (await cat.public(entry.id)).name == "North Studio"
        await cat.moderate(900, p.id, p.version, False, "Insufficient description")
        assert (await cat.public(entry.id)).name == "North Studio"


async def test_soft_remove_request_keeps_original_user_and_audit(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        p = await cat.begin(42, "REMOVE", entry_id=entry.id)
        p = await cat.submit(42, p.id, p.version)
        await cat.moderate(900, p.id, p.version, True)
        assert (await cat.repo.mine(42)).status == "REMOVED"
        assert await cat.core.repo.user_by_telegram(42) is not None
        assert (await cat.history(900, entry.id))[1] > 0
        assert (await cat.listing())[1] == 0


async def test_scam_suspension_restore_and_disabled_registration(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        entry = await cat.change_status(900, entry.id, entry.version, "SUSPENDED", "Policy review")
        assert (await cat.listing())[1] == 0
        entry = await cat.change_status(900, entry.id, entry.version, "APPROVED")
        owner = await cat.core.repo.user_by_telegram(42)
        s.add(ScamRecord(target_id=owner.id, reason="Confirmed fraud", moderator_id=900))
        await s.commit()
        assert (await cat.listing())[1] == 0
        entry = await cat.change_status(900, entry.id, entry.version, "SUSPENDED", "SCAM record")
        with pytest.raises(DomainError, match="merchant_blocked"):
            await cat.change_status(900, entry.id, entry.version, "APPROVED")
        await s.rollback()
        row = await cat.settings()
        await cat.configure(900, row.version, "registrations", False)
        with pytest.raises(DomainError, match="merchant_disabled"):
            await cat.begin(43, consent=True)


async def test_stale_draft_preview_expiry_and_daily_limit(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await cat.begin(42, consent=True)
        pid = p.id
        assert (await cat.begin(42, consent=True)).id == pid
        p = await cat.update_draft(42, pid, "name", "Current Name", p.version)
        with pytest.raises(DomainError, match="merchant_stale"):
            await cat.submit(42, pid, p.version - 1)
        await s.rollback()
        p = await s.get(MerchantProposal, pid)
        p.expires_at = now() - timedelta(seconds=1)
        await s.commit()
        with pytest.raises(DomainError, match="merchant_expired"):
            await cat.update_draft(42, pid, "city_code", "vilnius", p.version)
        await s.rollback()
        p = await s.get(MerchantProposal, pid)
        await cat.cancel(42, pid, p.version)
        for _ in range(2):
            p = await cat.begin(42, consent=True)
            await cat.cancel(42, p.id, p.version)
        with pytest.raises(DomainError, match="merchant_rate"):
            await cat.begin(42, consent=True)


async def test_admin_manual_add_uses_existing_numeric_identity(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        with pytest.raises(DomainError, match="merchant_identity"):
            await cat.begin(900, target_id=8888888, consent=True)
        await s.rollback()
        p = await cat.begin(900, target_id=43, consent=True)
        assert (await cat.repo.mine(43)).id == p.entry_id
        with pytest.raises(DomainError, match="forbidden"):
            await cat.begin(42, target_id=43, consent=True)


async def test_settings_are_authorized_versioned_and_durable(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        assert not (await cat.settings()).publish_authors
        row = await cat.configure(900, 1, "publish_authors", True)
        await cat.configure(900, row.version, "title", "Approved directory", lang="en")
        with pytest.raises(DomainError, match="forbidden"):
            await cat.configure(42, 1, "enabled", False)
        await s.rollback()
    async with database() as s:
        row = await s.get(MerchantSettings, 1)
        assert row.publish_authors and row.copy["en"]["title"] == "Approved directory"


async def test_postgres_concurrent_approval_has_one_audit_and_one_listing(
    postgres_contract, settings
):
    import asyncio

    sessions, _ = postgres_contract
    async with sessions() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        pid, version = p.id, p.version

    async def approve():
        async with sessions() as s:
            cat = MerchantService(Service(settings, s))
            return await cat.moderate(900, pid, version, True)

    await asyncio.gather(approve(), approve())
    async with sessions() as s:
        assert (
            await s.scalar(
                select(func.count())
                .select_from(MerchantEntry)
                .where(MerchantEntry.status == "APPROVED")
            )
            == 1
        )
        assert (
            await s.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "merchant_approved")
            )
            == 1
        )


async def test_username_reassignment_keeps_listing_and_rep_with_numeric_owner(database, settings):
    from test_merchants_data import merchant

    async with database() as s:
        entry, owner = await merchant(s, settings, 42, "Studio")
        cat = MerchantService(Service(settings, s))
        await cat.core.observe(42, "new_seller", "Owner")
        await cat.core.observe(43, "seller42", "New username owner")
        await s.commit()
        assert (await cat.repo.mine(42)).id == entry.id
        assert (await cat.listing(query="seller42"))[1] == 0
        rows, total, _ = await cat.listing(query="NEW_SELLER")
        assert total == 1 and rows[0][1].telegram_id == 42


async def test_approval_of_edits_updates_search_index_without_changing_trust(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        p = await cat.begin(42, "EDIT", entry_id=entry.id)
        p = await cat.update_draft(42, p.id, "name", "Baltic Works", p.version)
        p = await cat.update_draft(42, p.id, "city_code", "kaunas", p.version)
        p = await cat.submit(42, p.id, p.version)
        await cat.moderate(900, p.id, p.version, True)
        assert (await cat.listing(query="north"))[1] == 0
        assert (await cat.listing(city_code="kaunas", query="tic wor"))[1] == 1


async def test_approved_edit_never_resumes_a_suspended_entry(database, settings):
    async with database() as s:
        cat = await setup(s, settings)
        p = await submitted(cat)
        await cat.moderate(900, p.id, p.version, True)
        entry = await cat.repo.mine(42)
        entry = await cat.change_status(900, entry.id, entry.version, "SUSPENDED", "Policy review")
        p = await cat.begin(42, "EDIT", entry_id=entry.id)
        p = await cat.update_draft(42, p.id, "name", "Reviewed New Name", p.version)
        p = await cat.submit(42, p.id, p.version)
        await cat.moderate(900, p.id, p.version, True)
        assert (await cat.repo.mine(42)).status == "SUSPENDED"
        assert (await cat.listing())[1] == 0


async def test_postgres_registration_double_click_creates_one_durable_draft(
    postgres_contract, settings
):
    import asyncio

    sessions, _ = postgres_contract
    async with sessions() as s:
        await setup(s, settings)

    async def begin():
        async with sessions() as s:
            return (await MerchantService(Service(settings, s)).begin(42, consent=True)).id

    ids = await asyncio.gather(begin(), begin())
    assert ids[0] == ids[1]
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(MerchantProposal)) == 1

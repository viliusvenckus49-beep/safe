"""Adversarial security regressions; only isolated databases and fake Telegram transport."""

import pytest
from sqlalchemy import func, select

from app.admin_services import AdminService
from app.errors import DomainError
from app.models import AuditEvent, ReputationAdjustment, TopVisibility
from app.services import Service

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "operation",
    [
        "add_scam",
        "remove_scam",
        "moderate",
        "moderate_reputation",
        "adjust_rep",
        "top_visibility",
        "reset_all",
    ],
)
async def test_revocation_committed_before_write_lock_prevents_admin_mutation(
    database, settings, monkeypatch, operation
):
    """A checked grant is not valid after an owner revokes it while the update waits."""
    async with database() as session:
        service = Service(settings, session)
        await service.access.change(900, "901", True, "a" * 32)
        await service.resolve("42")
        reference = None
        if operation == "remove_scam":
            await service.add_scam(900, "42")
        elif operation == "moderate":
            reference = (
                await service.submit_report(
                    1, "42", "Documented payment incident", [], "report-seed"
                )
            ).reference
        elif operation == "moderate_reputation":
            reference = (
                await service.vote(1, "42", 1, None, "rep-seed", comment="Documented transaction")
            )["reputation_request"].reference
        elif operation == "reset_all":
            await service.admin_adjust_rep(900, "42", 3, "Verified transaction history", "seed")

        audit_count = await session.scalar(select(func.count()).select_from(AuditEvent))
        # Finish preparation so the injected revocation does not share this transaction.
        await session.commit()
        lock_name = "lock_identity_metadata"
        original_lock = getattr(service.repo, lock_name)
        calls = 0
        revoked = False

        async def revoke_before_lock(*args):
            nonlocal calls, revoked
            calls += 1
            # Target resolution commits its own metadata transaction first.
            trigger = (
                2 if operation in {"add_scam", "remove_scam", "adjust_rep", "top_visibility"} else 1
            )
            if calls == trigger:
                async with database() as owner_session:
                    await AdminService(settings, owner_session).change(900, "901", False, "b" * 32)
                revoked = True
            return await original_lock(*args)

        monkeypatch.setattr(service.repo, lock_name, revoke_before_lock)
        with pytest.raises(DomainError, match="forbidden"):
            if operation == "add_scam":
                await service.add_scam(901, "42")
            elif operation == "remove_scam":
                await service.remove_scam(901, "42", "Verified appeal evidence")
            elif operation == "moderate":
                await service.moderate(901, reference, True)
            elif operation == "moderate_reputation":
                await service.moderate_reputation(901, reference, True)
            elif operation == "adjust_rep":
                await service.admin_adjust_rep(
                    901, "42", 10, "Verified transaction history", "adjust"
                )
            elif operation == "top_visibility":
                await service.set_top_visibility(
                    901, "42", True, "Verified transaction history", "top"
                )
            else:
                await service.admin_reset_all_reputation(901, "Verified reset reason", "reset")
        assert revoked
        await session.rollback()

    async with database() as verification:
        service = Service(settings, verification)
        assert not await service.is_admin(901)
        profile = await service.profile("42")
        assert bool(profile["scam"]) == (operation == "remove_scam")
        assert profile["score"] == (3 if operation == "reset_all" else 0)
        if operation == "moderate":
            assert (await service.report_details(900, reference))["report"].status == "PENDING"
        if operation == "moderate_reputation":
            assert (await service.reputation_details(900, reference)).status == "PENDING"
        assert await verification.scalar(select(func.count()).select_from(TopVisibility)) == 0
        assert await verification.scalar(
            select(func.count()).select_from(ReputationAdjustment)
        ) == (1 if operation == "reset_all" else 0)
        assert (
            await verification.scalar(select(func.count()).select_from(AuditEvent))
            == audit_count + 1
        )


async def test_cached_access_is_refreshed_after_external_revocation(database, settings):
    async with database() as first, database() as owner:
        access = AdminService(settings, first)
        await access.change(900, "901", True, "a" * 32)
        row = await access.repo.administrator(901)
        assert row.active and await access.is_admin(901)
        await first.commit()
        await AdminService(settings, owner).change(900, "901", False, "b" * 32)
        assert not await access.is_admin(901)
        assert not row.active

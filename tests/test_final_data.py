"""Final data audit regressions; all identities and databases are test-only."""

from datetime import UTC

import pytest
from sqlalchemy import func, select
from test_integration_contracts import postgres_contract as postgres_fixture

from app.errors import DomainError
from app.models import AuditEvent, BanAction, ManagedGroup, User
from app.scam_management import ScamManagement
from app.services import Service

postgres_contract = postgres_fixture


@pytest.fixture(params=["sqlite", "postgresql"])
def data_contract(request):
    if request.param == "postgresql":
        return request.getfixturevalue("postgres_contract")
    return request.getfixturevalue("database"), request.getfixturevalue("settings")


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_numeric_target", [False, True])
async def test_id_supplement_rejects_a_different_observed_username_owner(
    data_contract, existing_numeric_target
):
    """An old username-only record must not create a second current numeric owner."""
    database, settings = data_contract
    async with database() as session:
        core = Service(settings, session)
        record = await core.add_scam(900, "@historicalname")
        record_id, original_target = record.id, record.target_id
        await core.observe(41, "historicalname", "Observed current owner")
        if existing_numeric_target:
            await core.resolve("42")
        session.add(
            ManagedGroup(
                chat_id=-100,
                title="Test group",
                approved=True,
                enabled=True,
                can_restrict_members=True,
            )
        )
        await session.commit()

        with pytest.raises(DomainError, match="sm_conflict"):
            await ScamManagement(core).supplement(900, record_id, "id", "42", "a" * 32)

        refreshed = await ScamManagement(core).detail(900, record_id)
        assert refreshed.target_id == original_target
        assert refreshed.target.telegram_id is None
        assert (await core.repo.user_by_telegram(41)).username == "historicalname"
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_identity_supplemented")
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(User)
                .where(User.telegram_id.is_not(None), User.username == "historicalname")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_id_supplement_allows_the_matching_observed_owner(data_contract):
    """Explicit matching-ID confirmation transfers only the selected SCAM record."""
    database, settings = data_contract
    async with database() as session:
        core = Service(settings, session)
        unknown = await core.resolve("@matchingowner")
        original_target = unknown.id
        await core.admin_adjust_rep(900, "@matchingowner", 7, "Test historical evidence", "old")
        report = await core.submit_report(7, "@matchingowner", "Test original report", [], "report")
        original_report_id = report.id
        record = await core.add_scam(900, "@matchingowner")
        record_id, created_at = record.id, record.created_at
        current = await core.observe(41, "matchingowner", "Test observed owner")

        confirmed = await ScamManagement(core).supplement(900, record_id, "id", "41", "b" * 32)

        assert confirmed.target_id == current.id
        assert confirmed.target.telegram_id == 41
        assert confirmed.created_at.replace(tzinfo=UTC) == created_at.replace(tzinfo=UTC)
        assert confirmed.moderator_id == 900
        assert (await core.repo.rep_stats(original_target))[0] == 7
        assert (await core.repo.rep_stats(current.id))[0] == 0
        preserved_report = await core.repo.report_by_request("report")
        assert preserved_report.id == original_report_id
        assert preserved_report.target_id == original_target
        assert await core.repo.user_by_id(original_target) is not None
        assert (await core.profile("41"))["scam"].id == record_id

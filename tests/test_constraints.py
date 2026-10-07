import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Report, ReportEvidence, ReputationEvent, ScamRecord, User


@pytest.mark.asyncio
@pytest.mark.parametrize("value,self_vote", [(0, False), (2, False), (1, True)])
async def test_rep_database_constraints(database, value, self_vote):
    async with database() as session:
        a, b = User(telegram_id=1), User(telegram_id=2)
        session.add_all([a, b])
        await session.flush()
        session.add(
            ReputationEvent(
                giver_user_id=a.id,
                receiver_user_id=a.id if self_vote else b.id,
                value=value,
                request_key="key",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_duplicate_active_scam_constraint(database):
    async with database() as session:
        user = User(telegram_id=1)
        session.add(user)
        await session.flush()
        session.add_all(
            [
                ScamRecord(target_id=user.id, reason="Reason", moderator_id=900),
                ScamRecord(target_id=user.id, reason="Reason", moderator_id=900),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_removed_scam_history_allows_new_active(database):
    async with database() as session:
        user = User(telegram_id=1)
        session.add(user)
        await session.flush()
        session.add_all(
            [
                ScamRecord(
                    target_id=user.id, reason="Historical", moderator_id=900, status="REMOVED"
                ),
                ScamRecord(target_id=user.id, reason="Current", moderator_id=900),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_evidence_foreign_key_enforced(database):
    async with database() as session:
        session.add(ReportEvidence(report_id=999, kind="photo", file_id="test"))
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_report_status_constraint(database):
    async with database() as session:
        a, b = User(telegram_id=1), User(telegram_id=2)
        session.add_all([a, b])
        await session.flush()
        session.add(
            Report(
                reference="SC-2026-000001",
                reporter_id=a.id,
                target_id=b.id,
                reason="Reason",
                request_key="r",
                status="SCAM",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()

from datetime import timedelta

import pytest

from app.models import BanAction, ManagedGroup, now
from app.repositories import Repository
from app.services import Service


@pytest.mark.asyncio
async def test_operational_counts_and_late_outbox_are_read_only(database, settings):
    async with database() as session:
        core = Service(settings, session)
        empty = await core.repo.operational_stats()
        assert empty == {
            "ban_terminal": 0,
            "recovery_terminal": 0,
            "oldest_pending_seconds": 0,
            "pending_reports": 0,
            "pending_rep": 0,
        }
        record = await core.add_scam(900, "42")
        session.add(ManagedGroup(chat_id=-123, title="Private group", approved=True))
        await session.flush()
        session.add(
            BanAction(
                chat_id=-123, telegram_id=42, scam_record_id=record.id, status="FAILED", attempts=8
            )
        )
        session.add(
            BanAction(
                chat_id=-123,
                telegram_id=43,
                scam_record_id=record.id,
                status="PROCESSING",
                claimed_at=now() - timedelta(minutes=10),
            )
        )
        await session.commit()
        await core.submit_report(1, "44", "Test-only report evidence", [], "report-test")
        await core.vote(2, "44", 1, None, "vote-test", comment="Test-only reason")
        data = await Repository(session).operational_stats()
        assert data["ban_terminal"] == 1 and data["recovery_terminal"] == 0
        assert 590 <= data["oldest_pending_seconds"] <= 610
        assert data["pending_reports"] == 1 and data["pending_rep"] == 1
        assert all(isinstance(value, int) for value in data.values())
        assert not session.new and not session.dirty

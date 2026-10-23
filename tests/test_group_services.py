import pytest
from sqlalchemy import func, select

from app.group_services import GroupService
from app.models import BanAction
from app.services import DomainError, Service

pytestmark = pytest.mark.asyncio


async def test_registration_and_confirmed_only_ban_outbox(database, settings):
    async with database() as session:
        svc, groups = Service(settings, session), GroupService(settings, session)
        await svc.add_scam(900, "12", "Patvirtinti nusikaltimo įrodymai")
        await svc.add_scam(900, "@unknown_user", "Nepatvirtinta Telegram tapatybė")
        await groups.register_group(900, -100, "Grupė", True)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        await groups.register_group(900, -100, "Grupė", True)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        await svc.submit_report(1, "13", "Mokėjimo pranešimas su įrodymais", [], "report")
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        await svc.add_scam(900, "14", "Patvirtintas papildomas įrašas")
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 2
        with pytest.raises(DomainError, match="forbidden"):
            await groups.register_group(1, -200, "Neleistina", True)


async def test_ban_retry_obsolete_and_reactivation(database, settings):
    async with database() as session:
        svc, groups = Service(settings, session), GroupService(settings, session)
        await groups.register_group(900, -100, "Grupė", True)
        await svc.add_scam(900, "12", "Patvirtintas sukčiavimo įrašas")
        jobs = await groups.claim_bans()
        assert len(jobs) == 1 and jobs[0].status == "PROCESSING"
        assert await groups.claim_bans() == []
        await groups.finish_ban(jobs[0].id, False, "TelegramRetryAfter", retry_after=60)
        assert await groups.claim_bans() == []
        await svc.remove_scam(900, "12", "Įrašo pašalinimo sprendimas")
        jobs[0].next_attempt_at = jobs[0].claimed_at
        await session.commit()
        assert await groups.claim_bans() == []
        await session.refresh(jobs[0])
        assert jobs[0].status == "OBSOLETE"
        await svc.add_scam(900, "12", "Naujas patvirtintas sukčiavimo įrašas")
        assert len(await groups.claim_bans()) == 1


async def test_claim_revalidation_and_exhausted_ban_rearm(database, settings):
    async with database() as session:
        svc, groups = Service(settings, session), GroupService(settings, session)
        await groups.register_group(900, -100, "Grupė", True)
        await svc.add_scam(900, "12", "Patvirtintas sukčiavimo įrašas")
        job = (await groups.claim_bans())[0]
        await groups.finish_ban(job.id, False, "TelegramBadRequest", permanent=True)
        await session.refresh(job)
        assert job.attempts == 8
        await groups.check_member(-100, 12)
        # The first observed presence immediately retries a rejected preemptive ban.
        assert len(await groups.claim_bans()) == 1
        await svc.remove_scam(900, "12", "Patvirtintas pašalinimo sprendimas")
        assert not await groups.ban_eligible(job.id)
        await session.refresh(job)
        assert job.status == "OBSOLETE"

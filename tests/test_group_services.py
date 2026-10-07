import pytest
from sqlalchemy import func, select

from app.group_services import GroupService
from app.models import BanAction, RecoveryDelivery
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


async def test_recovery_requires_observation_private_start_and_consent(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Matyta grupė", True)
        await groups.register_group(900, -200, "Privati nematyta grupė", True)
        with pytest.raises(DomainError, match="not_found"):
            await groups.subscribe(12, -100, True)
        await groups.mark_private_contact(12)
        with pytest.raises(DomainError, match="forbidden"):
            await groups.subscribe(12, -100, True)
        await groups.observe_member(-100, 12, "some_user", "Vardas")
        assert [g.chat_id for g in await groups.groups_for_subscription(12)] == [-100]
        await groups.subscribe(12, -100, True)
        preview = await groups.prepare_recovery(900, -100, "https://t.me/+Invite123", "campaign")
        assert preview["recipients"] == 1
        assert (await groups.queue_recovery(900, -100, "https://t.me/+Invite123", "campaign"))[
            "queued"
        ] == 1
        assert (await groups.queue_recovery(900, -100, "https://t.me/+Invite123", "campaign"))[
            "queued"
        ] == 0
        await groups.subscribe(12, -100, False)
        assert await groups.claim_deliveries() == []
        delivery = await session.scalar(select(RecoveryDelivery))
        assert delivery.status == "OBSOLETE"
        backup = await groups.export_members(900, -100)
        assert backup[0]["telegram_id"] == 12
        with pytest.raises(DomainError, match="forbidden"):
            await groups.export_members(1, -100)


@pytest.mark.parametrize(
    "url",
    [
        "http://t.me/+ok",
        "https://evil.test/+ok",
        "https://t.me.evil.test/+ok",
        "https://t.me/+ok?secret=a",
    ],
)
async def test_recovery_rejects_invalid_invite(database, settings, url):
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Grupė", True)
        with pytest.raises(DomainError, match="invalid_input"):
            await groups.prepare_recovery(900, -100, url, "key")


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


async def test_unsubscribe_refreshes_reused_session_consent(database, settings):
    from app.models import RecoverySubscription

    async with database() as setup:
        groups = GroupService(settings, setup)
        await groups.register_group(900, -100, "Grupė", True)
        await groups.mark_private_contact(12)
        await groups.observe_member(-100, 12, "some_user", "Vardas")
        await groups.subscribe(12, -100, False)
    async with database() as first, database() as second:
        cached = await first.get(RecoverySubscription, (-100, 12))
        assert cached.consent is False
        await first.commit()
        await GroupService(settings, second).subscribe(12, -100, True)
        await GroupService(settings, first).subscribe(12, -100, False)
    async with database() as verify:
        subscription = await verify.get(RecoverySubscription, (-100, 12))
        assert subscription.consent is False

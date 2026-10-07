import pytest
from sqlalchemy import func, select

from app.group_services import GroupService
from app.models import BanAction, ObservedMember
from app.services import DomainError, Service

pytestmark = pytest.mark.asyncio


async def test_other_admin_cannot_manage_groups(database, settings):
    settings.admin_ids = "900,901"
    settings.group_owner_id = 900
    async with database() as session:
        service = GroupService(settings, session)
        for operation in (
            service.stage_group(901, -100, "Other"),
            service.register_group(901, -100, "Other", True),
            service.groups(901),
            service.export_members(901, -100),
            service.prepare_recovery(901, -100, "https://t.me/+Invite123", "request"),
        ):
            with pytest.raises(DomainError, match="forbidden"):
                await operation


async def test_pending_group_no_observation_bans_or_subscription(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        staged = await groups.stage_group(900, -100, "Pending")
        assert not staged.approved and not staged.enabled
        await groups.observe_member(-100, 12, "someone", "Somebody")
        assert await session.scalar(select(func.count()).select_from(ObservedMember)) == 0
        await Service(settings, session).add_scam(900, "12", "Confirmed scam evidence")
        await groups.check_member(-100, 12, True)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0
        await groups.mark_private_contact(12)
        assert await groups.groups_for_subscription(12) == []
        with pytest.raises(DomainError, match="forbidden"):
            await groups.subscribe(12, -100, True)
        with pytest.raises(DomainError, match="not_found"):
            await groups.prepare_recovery(900, -100, "https://t.me/+Invite123", "request")
        await groups.register_group(900, -100, "Approved", True)
        await groups.register_group(900, -100, "Approved", True)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        restaged = await groups.stage_group(900, -100, "Pending again")
        assert restaged.approved and restaged.enabled


async def test_disabled_group_recovery_and_pending_revocation(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        group = await groups.register_group(900, -100, "Original", True)
        await groups.observe_member(-100, 12, "someone", "Somebody")
        await groups.mark_private_contact(12)
        await groups.subscribe(12, -100, True)
        await groups.disable_group(-100)
        assert (await groups.queue_recovery(900, -100, "https://t.me/+Invite123", "request"))[
            "queued"
        ] == 1
        jobs = await groups.claim_deliveries()
        assert len(jobs) == 1 and await groups.delivery_eligible(jobs[0].id)
        group.approved = False
        await session.commit()
        assert not await groups.delivery_eligible(jobs[0].id)
        assert await groups.subscribe(12, -100, False) is False

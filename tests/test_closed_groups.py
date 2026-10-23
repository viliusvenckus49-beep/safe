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
        await groups.register_group(900, -100, "Approved", True)
        await groups.register_group(900, -100, "Approved", True)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        restaged = await groups.stage_group(900, -100, "Pending again")
        assert restaged.approved and restaged.enabled

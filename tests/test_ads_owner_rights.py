"""Owner publishing uses approved bot access; other administrators keep live local ACLs."""

from datetime import timedelta

import pytest
from test_ads_publication import Bot, configured, snapshot
from test_ads_publication import clock as clock
from test_auto_ads_unified import assign, create, groups
from test_integration_contracts import postgres_contract as postgres_contract

import app.ads_global as global_module
import app.ads_unified as unified_module
from app.ads_publication import PublicationService, publication_rights
from app.ads_unified import UnifiedAds
from app.bot.ads_publisher import AdsPublisher
from app.errors import DomainError
from app.group_services import GroupService
from app.models import AdAssignmentPolicy, ManagedGroup
from app.moderation_services import utc


class RightsBot(Bot):
    def __init__(self):
        super().__init__()
        self.role_reads = []

    async def get_chat_member(self, chat, identity):
        self.role_reads.append((chat, identity))
        return await super().get_chat_member(chat, identity)


@pytest.mark.parametrize("status", ["member", "left", "restricted", "kicked"])
async def test_owner_does_not_need_personal_group_membership(status):
    bot = RightsBot()
    bot.roles[900] = status
    await publication_rights(bot, -100, 900, owner_id=900)
    assert bot.role_reads == [(-100, bot.id)]


async def test_non_owner_keeps_live_telegram_administrator_requirement():
    bot = RightsBot()
    bot.roles[900] = "member"
    with pytest.raises(DomainError, match="ap_rights"):
        await publication_rights(bot, -100, 900, owner_id=999)
    assert bot.role_reads == [(-100, 900)]


async def owner_lifecycle(database, settings, clock, monkeypatch):
    settings.admin_ids, settings.group_owner_id = "999", 900
    settings.ads_global_messages_per_second = settings.ads_group_messages_per_minute = 0
    monkeypatch.setattr(unified_module, "now", lambda: clock[0])
    monkeypatch.setattr(global_module, "now", lambda: clock[0])
    first = await create(database, settings)
    other = await create(database, settings, "Other ad", "other")
    await groups(database)
    bot = RightsBot()
    bot.roles[900] = "member"
    async with database() as session:
        management = UnifiedAds(settings, session)
        await management.policy(900, other)
        policy = await management.policy(900, first)
        await management.groups(900, first, policy.version, [-100, -200], bot, all_groups=True)
        policy = await management.policy(900, first)
        await management.change(900, first, policy.version, "interval_seconds", 7200, bot)
        policy = await management.policy(900, first)
        await management.lifecycle(900, first, policy.version, "START", bot)
        schedules = await management.schedules(first)
        assert len(schedules) == 2 and all(s.status == "ACTIVE" for s in schedules)
        due = min(utc(s.next_run_at) for s in schedules)
        assert due > clock[0]
        assert not await management.schedules(other)
        assert (await session.get(AdAssignmentPolicy, other)).interval_seconds == 3600
    assert not bot.calls
    await AdsPublisher(bot, settings, database).tick()
    assert not bot.calls
    clock[0] = due + timedelta(seconds=1)
    await AdsPublisher(bot, settings, database).tick()
    assert {args["chat_id"] for _, args in bot.calls} == {-100, -200}
    assert len(bot.calls) == 2 and all(identity == bot.id for _, identity in bot.role_reads)


async def test_independent_owner_can_edit_activate_and_send_only_at_next_slot(
    database, settings, clock, monkeypatch
):
    await owner_lifecycle(database, settings, clock, monkeypatch)


async def test_postgres_independent_owner_without_personal_group_administration(
    postgres_contract, clock, monkeypatch
):
    database, settings = postgres_contract
    await owner_lifecycle(database, settings, clock, monkeypatch)


@pytest.mark.parametrize("revoked", ["approval", "enabled", "owner_removed", "bot", "owner"])
async def test_owner_rule_never_overrides_group_bot_or_current_owner_revocation(
    database, settings, clock, revoked
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    bot.roles[900] = "member"
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        claim = await pub.claim()
        assert claim is not None
        if revoked == "owner_removed":
            await GroupService(settings, session).remove_group(900, -100)
        elif revoked == "bot":
            bot.roles[bot.id] = "member"
        elif revoked == "owner":
            settings.group_owner_id = 999
        else:
            group = await session.get(ManagedGroup, -100)
            setattr(group, "approved" if revoked == "approval" else "enabled", False)
            await session.commit()
    await AdsPublisher(bot, settings, database).execute(claim)
    assert not any(args["chat_id"] < 0 for _, args in bot.calls)
    assert (await snapshot(database, settings))[0][0].status in {"CANCELLED", "FAILED"}


async def test_client_cannot_use_owner_publication_access(database, settings):
    cid = await create(database, settings)
    await groups(database)
    await assign(database, settings, cid)
    async with database() as session:
        management = UnifiedAds(settings, session)
        policy = await management.policy(900, cid)
        with pytest.raises(DomainError, match="forbidden"):
            await management.lifecycle(42, cid, policy.version, "START", Bot())


@pytest.mark.parametrize("field", ["approved", "enabled"])
async def test_owner_cannot_assign_unapproved_or_disabled_groups(database, settings, field):
    cid = await create(database, settings)
    await groups(database)
    bot = RightsBot()
    bot.roles[900] = "member"
    async with database() as session:
        setattr(await session.get(ManagedGroup, -100), field, False)
        await session.commit()
        management = UnifiedAds(settings, session)
        policy = await management.policy(900, cid)
        with pytest.raises(DomainError, match="ap_group"):
            await management.groups(900, cid, policy.version, [-100], bot)
        assert not await management.schedules(cid)

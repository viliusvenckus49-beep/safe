import pytest
from sqlalchemy import select
from test_telegram import journey as telegram_journey

from app.bot.callbacks import Action
from app.bot.keyboards import home
from app.group_services import GroupService
from app.i18n import CATALOGS, use_language
from app.models import ObservedMember, ScamRecord
from app.services import DomainError, Service

journey = telegram_journey


@pytest.mark.asyncio
async def test_active_scam_hidden_from_users_backup_but_history_preserved(database, settings):
    async with database() as session:
        service = Service(settings, session)
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Group", True)
        await groups.observe_member(-100, 42, "example", "Person")
        await groups.observe_member(-100, 43, "other", "Other")
        record = await service.add_scam(900, "42")
        assert record.reason == ""
        users, total = await service.users(900, 0)
        assert 42 not in [user.telegram_id for user in users] and total == 1
        assert [r["telegram_id"] for r in await groups.export_members(900, -100)] == [43]
        assert await session.get(ObservedMember, (-100, 42)) is not None
        await service.observe(42, "newname", "Changed")
        assert 42 not in [u.telegram_id for u in (await service.users(900, 0))[0]]
        await service.remove_scam(900, "42", "Reviewed incorrect record")
        assert 42 in [u.telegram_id for u in (await service.users(900, 0))[0]]
        assert 42 in [r["telegram_id"] for r in await groups.export_members(900, -100)]


@pytest.mark.asyncio
async def test_scam_member_not_newly_saved_but_ban_still_queued(database, settings):
    async with database() as session:
        service = Service(settings, session)
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Group", True)
        await service.add_scam(900, "42")
        await groups.observe_member(-100, 42, "example", "Person")
        assert await session.get(ObservedMember, (-100, 42)) is None
        assert await groups.export_members(900, -100) == []
        from app.models import BanAction

        assert (
            await session.scalar(select(BanAction).where(BanAction.telegram_id == 42)) is not None
        )


@pytest.mark.asyncio
async def test_username_only_scam_filter_case_normalization(database, settings):
    async with database() as session:
        service = Service(settings, session)
        groups = GroupService(settings, session)
        await groups.register_group(900, -100, "Group", True)
        await groups.observe_member(-100, 42, "EXAMPLE", "Person")
        # Existing historical unverified username record, independent of numeric identity.
        from app.models import User

        target = User(username="example", display_name="Unknown")
        session.add(target)
        await session.flush()
        session.add(ScamRecord(target_id=target.id, reason="", moderator_id=900, status="ACTIVE"))
        await session.commit()
        assert await groups.export_members(900, -100) == []
        assert not (await service.users(900, 0))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["42", "@example"])
async def test_admin_add_scam_without_reason_private_and_group(journey, database, settings, target):
    await journey.send("/add_sc " + target, actor=900, chat=-100)
    async with database() as session:
        record = await session.scalar(select(ScamRecord))
        assert record is not None and record.reason == ""
    await journey.send("/add_sc 43", actor=1, chat=-100)
    async with database() as session:
        with pytest.raises(DomainError, match="forbidden"):
            await Service(settings, session).add_scam(1, "43")


@pytest.mark.asyncio
async def test_add_scam_target_wizard_needs_no_reason(journey, database):
    await journey.send("/add_sc", actor=900)
    await journey.send("42", actor=900)
    async with database() as session:
        assert (await session.scalar(select(ScamRecord))).reason == ""
    assert await journey.state(900) is None


def test_scam_menu_and_admin_entry_private_only():
    for private in [False, True]:
        values = [
            b.callback_data for row in home(True, private=private).inline_keyboard for b in row
        ]
        assert (Action(name="admin").pack() in values) is private
        assert (
            Action(name="scams", value="0").pack() in values
            if private
            else Action(name="scams", value="0").pack() not in values
        )


@pytest.mark.asyncio
async def test_group_scam_command_and_old_callback_do_not_dump_registry(
    journey, database, settings
):
    async with database() as session:
        await Service(settings, session).add_scam(900, "@example")
    await journey.send("/scammers", chat=-100)
    await journey.click(Action(name="scams", value="0").pack(), chat=-100)
    assert "@example" not in journey.text()
    await journey.send("/scammers")
    assert "@example" in journey.text()


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_information_explains_lookup_and_private_report(lang):
    with use_language(lang):
        text = CATALOGS[lang]["p.info"]
        assert "/ask @username" in text and "/ask 123456789" in text and "/report" in text


def test_retry_control_uses_numeric_record_and_requires_known_id():
    from types import SimpleNamespace

    from app.bot.callbacks import ScamAdmin
    from app.bot.scam_admin import controls

    record = SimpleNamespace(
        id=44, target=SimpleNamespace(telegram_id=5108847812, username="brodvejus")
    )
    callbacks = [button.callback_data for row in controls(record).inline_keyboard for button in row]
    assert ScamAdmin(action="retry", value="44").pack() in callbacks
    record.target.telegram_id = None
    callbacks = [button.callback_data for row in controls(record).inline_keyboard for button in row]
    assert ScamAdmin(action="retry", value="44").pack() not in callbacks

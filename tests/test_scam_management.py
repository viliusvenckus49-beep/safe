import pytest
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app.bot.callbacks import Action, ScamAdmin
from app.bot.states import ScamAdminFlow
from app.errors import DomainError
from app.models import AuditEvent, ScamRecord
from app.scam_management import ScamManagement
from app.services import Service

journey = telegram_journey


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["42", "@unknownscam"])
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_registry_remove_is_direct_and_preserves_history(
    journey, database, settings, target, lang
):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        await core.admin_adjust_rep(900, target, 3, "Reviewed reputation evidence", "remove-rep")
        await core.set_trusted(900, target, True, "remove-trusted")
        record = await core.add_scam(900, target)
        record_id, target_id = record.id, record.target_id
    await journey.click(Action(name="scams", value="0").pack(), actor=900)
    assert any(
        button.callback_data == ScamAdmin(action="view", value=str(record_id)).pack()
        for call in journey.transport.calls
        for row in getattr(getattr(call, "reply_markup", None), "inline_keyboard", [])
        for button in row
    )
    await journey.click(ScamAdmin(action="view", value=str(record_id)).pack(), actor=900)
    markup = journey.transport.calls[-1].reply_markup
    remove = ScamAdmin(action="remove", value=str(record_id)).pack()
    assert remove in [button.callback_data for row in markup.inline_keyboard for button in row]
    await journey.click(remove, actor=900)
    assert await journey.state(900) is None
    async with database() as session:
        core = Service(settings, session)
        archived = await core.repo.scam_by_id(record_id)
        assert archived.status == "REMOVED" and archived.removed_by == 900
        assert archived.removed_at and archived.removal_reason
        assert archived.target_id == target_id
        assert (await core.repo.rep_stats(target_id))[0] == 3
        assert (await core.repo.trusted_designation(target_id)).active
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_removed")
            )
            == 1
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["remove", "remove_receipt"])
async def test_remove_callback_requires_private_admin(journey, database, settings, action):
    async with database() as session:
        record = await Service(settings, session).add_scam(900, "42")
        record_id = record.id
    callback = ScamAdmin(action=action, value=str(record_id)).pack()
    await journey.click(callback, actor=1)
    await journey.click(callback, actor=900, chat=-123)
    async with database() as session:
        assert (await Service(settings, session).repo.scam_by_id(record_id)).status == "ACTIVE"
    await journey.click(callback, actor=900)
    async with database() as session:
        assert (await Service(settings, session).repo.scam_by_id(record_id)).status == "REMOVED"


@pytest.mark.asyncio
async def test_pinned_removal_does_not_resolve_or_remove_new_activation(
    database, settings, monkeypatch
):
    async with database() as session:
        core = Service(settings, session)
        unknown = await core.add_scam(900, "@unknownscam")
        target = f"u:{unknown.target_id}"
        original = core.resolve

        async def forbidden_lookup(*args, **kwargs):
            raise AssertionError("Removal must not resolve a username or initiate bans")

        monkeypatch.setattr(core, "resolve", forbidden_lookup)
        assert await core.remove_scam(900, target, "Registry removal", record_id=unknown.id)
        monkeypatch.setattr(core, "resolve", original)
        latest = await core.add_scam(900, target)
        assert latest.id != unknown.id
        monkeypatch.setattr(core, "resolve", forbidden_lookup)
        assert not await core.remove_scam(900, target, "Registry removal", record_id=unknown.id)
        assert (await core.repo.scam_by_id(latest.id)).status == "ACTIVE"


@pytest.mark.asyncio
async def test_removal_stops_pending_bans(database, settings):
    from app.group_services import GroupService

    async with database() as session:
        core = Service(settings, session)
        groups = GroupService(settings, session)
        await groups.register_group(900, -1000, "Protected", True)
        record = await core.add_scam(900, "42")
        jobs = await groups.claim_bans(scam_record_id=record.id)
        assert len(jobs) == 1
        assert await core.remove_scam(900, "42", "Registry removal", record_id=record.id)
        assert not await groups.ban_eligible(jobs[0].id)


def test_group_receipts_keep_refresh_only():
    from types import SimpleNamespace

    from app.bot.scam_admin import refresh_controls

    callbacks = [
        button.callback_data
        for row in refresh_controls(SimpleNamespace(id=42)).inline_keyboard
        for button in row
    ]
    assert callbacks == [ScamAdmin(action="retry_receipt", value="42").pack()]


@pytest.mark.asyncio
async def test_identity_binding_preserves_record_history_and_rep(database, settings):
    async with database() as session:
        core = Service(settings, session)
        unknown = await core.resolve("@someone")
        original_id = unknown.id
        await core.admin_adjust_rep(900, "@someone", 7, "Reviewed reputation evidence", "old-rep")
        record = await core.add_scam(900, "@someone")
        await core.observe(42, "someone", "Known person")
        manager = ScamManagement(core)
        result = await manager.supplement(900, record.id, "id", "42", "a" * 32)
        assert result.target.telegram_id == 42 and result.id == record.id
        assert result.created_at == record.created_at and result.moderator_id == 900
        assert (await core.repo.rep_stats(original_id))[0] == 7
        assert (await core.repo.rep_stats(result.target_id))[0] == 0
        assert (await core.profile("@someone"))["scam"].id == record.id
        await manager.supplement(900, record.id, "id", "42", "a" * 32)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_identity_supplemented")
            )
            == 1
        )
        with pytest.raises(DomainError):
            await manager.supplement(1, record.id, "id", "42", "a" * 32)
        with pytest.raises(DomainError):
            await manager.supplement(900, record.id, "id", "43", "a" * 32)


@pytest.mark.asyncio
async def test_missing_fields_conflicts_and_validation(database, settings):
    async with database() as session:
        core = Service(settings, session)
        manager = ScamManagement(core)
        record = await core.add_scam(900, "@someone")
        await manager.supplement(900, record.id, "id", "42", "a" * 32)
        assert (await core.profile("42"))["scam"].id == record.id
        for value in ["0", "-1", "²", "9" * 20, "<b>42</b>"]:
            with pytest.raises(DomainError):
                manager.validate("id", value)
        numeric = await core.add_scam(900, "43")
        await manager.supplement(900, numeric.id, "username", "@NewName", "b" * 32)
        assert (await core.profile("@newname"))["scam"].id == numeric.id
        with pytest.raises(DomainError):
            await manager.supplement(900, numeric.id, "username", "@othername", "c" * 32)
        unresolved = await core.add_scam(900, "@another")
        with pytest.raises(DomainError):
            await manager.supplement(900, unresolved.id, "id", "43", "d" * 32)
        await core.remove_scam(900, "@another", "Removed after review")
        with pytest.raises(DomainError):
            await manager.supplement(900, unresolved.id, "id", "44", "e" * 32)


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_private_command_then_plain_id_confirm_and_replay(journey, database, settings, lang):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
    await journey.send("/add_sc @someone", actor=900)
    assert await journey.state(900) == ScamAdminFlow.input.state
    await journey.send("8727262126", actor=900)
    data = await journey.data(900)
    assert await journey.state(900) == ScamAdminFlow.preview.state
    async with database() as session:
        assert (await Service(settings, session).profile("@someone"))["user"].telegram_id is None
    confirm = ScamAdmin(action="confirm", value=data["scam_nonce"]).pack()
    await journey.click(confirm, actor=900)
    await journey.click(confirm, actor=900)
    async with database() as session:
        core = Service(settings, session)
        assert (await core.profile("@someone"))["user"].telegram_id == 8727262126
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 1
    await journey.click(Action(name="admin_scams", value="0").pack(), actor=900)
    assert "8727262126" in journey.text()


@pytest.mark.asyncio
async def test_numeric_command_wizard_and_callback_authority(journey, database, settings):
    await journey.send("/add_sc 8727262126", actor=900)
    await journey.send("/add_sc", actor=900)
    await journey.send("8727262127", actor=900)
    async with database() as session:
        core = Service(settings, session)
        record = (await core.profile("8727262126"))["scam"]
        record_id = record.id
    callback = ScamAdmin(action="username", value=str(record_id)).pack()
    await journey.click(callback, actor=1)
    await journey.click(callback, actor=900, chat=-123)
    await journey.click(ScamAdmin(action="view", value="²").pack(), actor=900)
    await journey.click(callback, actor=900)
    await journey.send("@numericuser", actor=900)
    draft = await journey.data(900)
    await journey.click(ScamAdmin(action="confirm", value=draft["scam_nonce"]).pack(), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("@numericuser"))[
            "user"
        ].telegram_id == 8727262126


@pytest.mark.asyncio
async def test_identity_commit_failure_rolls_back(database, settings, monkeypatch):
    async with database() as session:
        core = Service(settings, session)
        record = await core.add_scam(900, "@rollbackscam")
        record_id = record.id
        original = session.commit

        async def fail():
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError):
            await ScamManagement(core).supplement(900, record_id, "id", "42", "f" * 32)
        monkeypatch.setattr(session, "commit", original)
        current = await ScamManagement(core).detail(900, record_id)
        assert current.target.telegram_id is None
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_identity_supplemented")
            )
            == 0
        )

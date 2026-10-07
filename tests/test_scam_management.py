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

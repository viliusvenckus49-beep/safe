import pytest
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app.bot.group_keyboards import GroupAction
from app.bot.keyboards import home
from app.i18n import CATALOGS
from app.models import AuditEvent, TrustedAction
from app.services import DomainError, Service

journey = telegram_journey


@pytest.mark.asyncio
async def test_manual_trusted_persistence_revoke_and_old_replay(database, settings):
    async with database() as session:
        service = Service(settings, session)
        data = await service.set_trusted(900, "42", True, "first")
        assert data["trusted"] and data["trusted_source"] == "manual"
        await service.set_trusted(900, "42", False, "second")
        data = await service.set_trusted(900, "42", True, "first")
        assert not data["trusted"] and data["trusted_request_replayed"]
        assert await session.scalar(select(func.count()).select_from(TrustedAction)) == 2
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 2
    async with database() as session:
        assert not (await Service(settings, session).profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_top_trusted_is_dynamic_and_manual_removal_does_not_remove_top(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.set_top_visibility(900, "42", True, "Explicit leaderboard inclusion", "top1")
        profile = await service.profile("42")
        assert profile["trusted"] and profile["trusted_source"] == "top"
        data = await service.set_trusted(900, "42", False, "manual-remove")
        assert data["trusted"] and data["trusted_source"] == "top"
        await service.set_top_visibility(900, "42", False, "Explicit leaderboard exclusion", "top2")
        assert not (await service.profile("42"))["trusted"]
        await service.set_trusted(900, "42", True, "manual-grant")
        assert (await service.profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_scam_overrides_manual_and_top_and_rejects_grant(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.set_trusted(900, "42", True, "trusted")
        await service.set_top_visibility(900, "42", True, "Explicit leaderboard inclusion", "top")
        await service.add_scam(900, "42")
        assert not (await service.profile("42"))["trusted"]
        with pytest.raises(DomainError, match="trusted_scam"):
            await service.set_trusted(900, "42", True, "regrant")
        await service.remove_scam(900, "42", "Reviewed false positive record")
        assert (await service.profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_manual_trusted_follows_numeric_identity_not_changed_username(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.observe(42, "original", "Name")
        await service.set_trusted(900, "@original", True, "first")
        await service.observe(42, "changed", "Name")
        assert (await service.profile("@changed"))["trusted"]
        await service.observe(43, "original", "Someone else")
        assert not (await service.profile("@original"))["trusted"]


@pytest.mark.asyncio
async def test_unknown_username_trusted_is_not_inherited_when_numeric_identity_appears(
    database, settings
):
    async with database() as session:
        service = Service(settings, session)
        await service.set_trusted(900, "@unknown", True, "first")
        assert (await service.profile("@unknown"))["trusted"]
        await service.observe(42, "unknown", "Observed person")
        assert not (await service.profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_permissions_noop_consumption_and_key_mismatch(database, settings):
    async with database() as session:
        service = Service(settings, session)
        with pytest.raises(DomainError, match="forbidden"):
            await service.set_trusted(1, "42", True, "first")
        await service.set_trusted(900, "42", False, "noop")
        await service.set_trusted(900, "42", True, "first")
        assert (await service.set_trusted(900, "42", False, "noop"))["trusted"]
        with pytest.raises(DomainError, match="stale_callback"):
            await service.set_trusted(900, "43", True, "first")


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_commands_lookup_display_and_nonadmin_denial(journey, database, settings, lang):
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.send("/add_trusted 42", actor=900)
    await journey.send("/ask 42", actor=900)
    assert CATALOGS[lang]["p.trusted_status"] in journey.text()
    await journey.send("/del_trusted 42", actor=900)
    await journey.send("/add_trusted 43", actor=1)
    async with database() as session:
        service = Service(settings, session)
        assert not (await service.profile("42"))["trusted"]
        assert not (await service.profile("43"))["trusted"]


def test_recovery_button_is_private_only():
    recovery = GroupAction(action="subscriptions").pack()
    for private in [False, True]:
        values = [
            button.callback_data for row in home(private=private).inline_keyboard for button in row
        ]
        assert (recovery in values) is private


@pytest.mark.asyncio
async def test_grant_rechecks_username_after_identity_lock(database, settings, monkeypatch):
    async with database() as session:
        service = Service(settings, session)
        await service.add_scam(900, "@blocked")
        await service.observe(42, "original", "Person")
        original_lock = service.repo.lock_identity_metadata
        calls = 0

        async def interleave():
            nonlocal calls
            calls += 1
            if calls == 2:
                async with database() as other:
                    await Service(settings, other).observe(42, "blocked", "Person")
            await original_lock()

        monkeypatch.setattr(service.repo, "lock_identity_metadata", interleave)
        with pytest.raises(DomainError, match="trusted_scam"):
            await service.set_trusted(900, "42", True, "grant")
        assert not (await service.profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_manual_trusted_qualifies_for_top_but_explicit_exclusion_wins(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.set_trusted(900, "42", True, "grant")
        assert [row["user"].telegram_id for row in await service.leaderboard()] == [42]
        await service.set_top_visibility(900, "42", False, "Owner hides trusted member", "hide")
        assert await service.leaderboard() == []
        assert (await service.profile("42"))["trusted_source"] == "manual"
        await service.set_top_visibility(900, "42", True, "Owner restores trusted member", "show")
        assert len(await service.leaderboard()) == 1
        await service.set_top_visibility(
            900, "42", False, "Owner hides trusted member", "hide-again"
        )
        await service.set_trusted(900, "42", False, "revoke")
        assert not (await service.profile("42"))["trusted"]


@pytest.mark.asyncio
async def test_role_top_eligibility_respects_revocation_and_scam(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.observe(42, "moderator", "Moderator")
        await service.access.change(900, "42", True, "a" * 32)
        assert [row["user"].telegram_id for row in await service.leaderboard()] == [42]
        await service.access.change(900, "42", False, "b" * 32)
        assert await service.leaderboard() == []
        await service.set_trusted(900, "42", True, "manual")
        await service.add_scam(900, "42")
        assert await service.leaderboard() == []


@pytest.mark.asyncio
async def test_top_prefers_observed_moderator_over_old_username_placeholder(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.set_trusted(900, "@cart3lis", True, "old-trusted")
        placeholder = await service.resolve("@cart3lis")
        await service.set_top_visibility(900, "@cart3lis", True, "Approved TOP member", "old-top")
        await service.observe(42, "cart3lis", "Moderator")
        await service.access.change(900, "42", True, "a" * 32)
        rows = await service.leaderboard()
        assert [row["user"].telegram_id for row in rows] == [42]
        assert (await service.profile(f"u:{placeholder.id}"))["trusted_source"] == "manual"
        await service.set_top_visibility(900, "42", False, "Hide observed moderator", "hide-known")
        assert await service.leaderboard() == []
        await service.observe(42, "changed", "Moderator")
        rows = await service.leaderboard()
        assert [row["user"].id for row in rows] == [placeholder.id]

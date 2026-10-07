import pytest
from sqlalchemy import func, select
from test_telegram import journey as telegram_journey

from app.bot import keyboards as kb
from app.bot.callbacks import TrustedAdmin
from app.bot.states import TrustedAdminFlow
from app.errors import DomainError
from app.i18n import t, use_language
from app.models import TopVisibilityAction, TrustedAction
from app.services import Service
from app.trusted_management import TrustedManagement

journey = telegram_journey


def cb(name, value=""):
    return TrustedAdmin(action=name, value=value).pack()


@pytest.mark.asyncio
async def test_pagination_search_manual_top_roles_and_permission(database, settings):
    async with database() as session:
        core = Service(settings, session)
        for i in range(1, 18):
            await core.observe(i, f"trusted{i}", f"Name {i}")
            await core.set_trusted(900, str(i), True, f"grant{i}")
        management = TrustedManagement(core)
        first, total, page = await management.page(900, 0)
        second, _, _ = await management.page(900, 1)
        last, _, page = await management.page(900, 999)
        assert total == 17 and [len(first), len(second), len(last)] == [8, 8, 1] and page == 2
        assert len({row["user"].id for row in first + second + last}) == 17
        found, total, _ = await management.page(900, 0, "@trusted17")
        assert total == 1 and found[0]["user"].telegram_id == 17
        assert (await management.page(900, 0, "%"))[1] == 0
        assert (await management.page(900, 0, "²"))[1] == 0
        with pytest.raises(DomainError):
            await management.page(1, 0)
        with pytest.raises(DomainError):
            await management.page(900, 0, "9" * 64)


@pytest.mark.asyncio
async def test_removal_atomic_replay_preserves_regrant_and_reputation(database, settings):
    async with database() as session:
        core = Service(settings, session)
        await core.set_trusted(900, "42", True, "grant")
        await core.set_top_visibility(900, "42", True, "Reviewed trusted person", "top")
        await core.admin_adjust_rep(900, "42", 5, "Reviewed reputation evidence", "rep")
        user = await core.resolve("42")
        management = TrustedManagement(core)
        result = await management.revoke(900, user.id, "a" * 32)
        assert not result["trusted"] and not result["manual_trusted"] and not result["top_included"]
        assert result["score"] == 5
        await core.set_trusted(900, "42", True, "regrant")
        await core.set_top_visibility(900, "42", True, "Reviewed restored person", "restore-top")
        replay = await management.revoke(900, user.id, "a" * 32)
        assert replay["manual_trusted"] and replay["top_included"] and replay["trusted"]
        with pytest.raises(DomainError):
            await management.revoke(1, user.id, "b" * 32)
        with pytest.raises(DomainError):
            await management.revoke(900, user.id, "invalid")
        with pytest.raises(DomainError):
            await management.revoke(900, (await core.resolve("43")).id, "a" * 32)


@pytest.mark.asyncio
async def test_role_only_not_removable_and_role_with_manual_remains(database, settings):
    async with database() as session:
        core = Service(settings, session)
        owner = await core.resolve("900")
        management = TrustedManagement(core)
        assert not (await management.detail(900, owner.id))["removable"]
        with pytest.raises(DomainError):
            await management.revoke(900, owner.id, "a" * 32)
        await core.set_trusted(900, "900", True, "manual-owner")
        data = await management.revoke(900, owner.id, "b" * 32)
        assert data["trusted"] and data["role"] == "founder" and not data["manual_trusted"]
        assert await core.is_admin(900)


@pytest.mark.asyncio
async def test_removal_rollback_leaves_both_sources_active(database, settings, monkeypatch):
    async with database() as session:
        core = Service(settings, session)
        await core.set_trusted(900, "42", True, "grant")
        await core.set_top_visibility(900, "42", True, "Reviewed trusted person", "top")
        user = await core.resolve("42")
        user_id = user.id
        original = session.commit

        async def fail():
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail)
        with pytest.raises(RuntimeError):
            await TrustedManagement(core).revoke(900, user.id, "a" * 32)
        monkeypatch.setattr(session, "commit", original)
        data = await TrustedManagement(core).detail(900, user_id)
        assert data["manual_trusted"] and data["top_included"]
        assert await core.repo.trusted_action("trusted-ui:" + "a" * 32) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_private_admin_journey_search_preview_cancel_remove_and_double_click(
    journey, database, settings, lang
):
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        await core.observe(42, "example", "<User>")
        await core.set_trusted(900, "42", True, "grant")
        user_id = (await core.resolve("42")).id
    await journey.click(kb.action("trusted_admin"), actor=900)
    with use_language(lang):
        assert t("tm.title") in journey.text()
    await journey.click(cb("search"), actor=900)
    assert await journey.state(900) == TrustedAdminFlow.search.state
    await journey.send("@example", actor=900)
    assert (await journey.data(900))["trusted_query"] == "@example"
    await journey.click(cb("view", str(user_id)), actor=900)
    await journey.click(cb("remove", str(user_id)), actor=900)
    nonce = (await journey.data(900))["trusted_nonce"]
    await journey.click(cb("cancel"), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["trusted"]
    await journey.click(cb("remove", str(user_id)), actor=900)
    current = (await journey.data(900))["trusted_nonce"]
    assert current != nonce
    await journey.click(cb("confirm", nonce), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["trusted"]
    await journey.click(cb("confirm", current), actor=900)
    await journey.click(cb("confirm", current), actor=900)
    async with database() as session:
        core = Service(settings, session)
        assert not (await core.profile("42"))["trusted"]
        assert await session.scalar(select(func.count()).select_from(TopVisibilityAction)) == 1
        assert await session.scalar(select(func.count()).select_from(TrustedAction)) == 2
    await journey.click(cb("page", "0"), actor=900)
    assert await journey.state(900) is None


@pytest.mark.asyncio
async def test_callback_permission_bypass_group_and_malformed(journey, database, settings):
    async with database() as session:
        core = Service(settings, session)
        await core.set_trusted(900, "42", True, "grant")
        target = (await core.resolve("42")).id
    for actor, chat in [(1, None), (900, -100)]:
        await journey.click(cb("remove", str(target)), actor=actor, chat=chat)
        assert await journey.state(actor) is None
    await journey.click(cb("page", "²"), actor=900)
    await journey.click(cb("view", "²"), actor=900)
    async with database() as session:
        assert (await Service(settings, session).profile("42"))["trusted"]

import pytest

from app import presentation as p
from app.i18n import t, use_language
from app.services import Service


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_owner_and_dynamic_moderator_roles_and_revocation(database, settings, lang):
    async with database() as session:
        service = Service(settings, session)
        owner = await service.profile("900")
        assert owner["role"] == "founder" and owner["trusted_source"] == "role"
        assert owner["trusted"] and owner["trusted_updated_at"] is None
        await service.observe(42, "moderator", "Moderator")
        assert not (await service.profile("42"))["trusted"]
        await service.access.change(900, "42", True, "a" * 32)
        moderator = await service.profile("@moderator")
        assert moderator["role"] == "moderator" and moderator["trusted_source"] == "role"
        with use_language(lang):
            for data in (owner, moderator):
                card = p.profile(data)
                assert t("p.role_" + data["role"]) not in card
                assert t("p.lookup_trusted_role") not in card
                assert t("p.lookup_trusted_manual") in card
                assert t("p.warning") not in card
        await service.observe(42, "changed", "Moderator")
        assert (await service.profile("@changed"))["role"] == "moderator"
        await service.observe(43, "moderator", "Someone else")
        assert (await service.profile("@moderator"))["role"] is None
        await service.access.change(900, "42", False, "b" * 32)
        revoked = await service.profile("42")
        assert revoked["role"] is None and not revoked["trusted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["manual", "top"])
async def test_role_revocation_preserves_independent_trusted_source(database, settings, source):
    async with database() as session:
        service = Service(settings, session)
        await service.access.change(900, "42", True, "a" * 32)
        if source == "manual":
            await service.set_trusted(900, "42", True, "manual")
        else:
            await service.set_top_visibility(900, "42", True, "Included by administrator", "top")
        await service.access.change(900, "42", False, "b" * 32)
        data = await service.profile("42")
        assert data["role"] is None and data["trusted"] and data["trusted_source"] == source


@pytest.mark.asyncio
async def test_scam_overrides_role_trusted_and_unknown_username_has_no_role(database, settings):
    async with database() as session:
        service = Service(settings, session)
        await service.access.change(900, "42", True, "a" * 32)
        await service.add_scam(900, "42")
        data = await service.profile("42")
        assert data["role"] == "moderator" and not data["trusted"]
        assert "TRUSTED" not in p.profile(data)
        unknown = await service.profile("@unknown")
        assert unknown["role"] is None and not unknown["trusted"]

"""Unresolved username matches are warnings, never automatic numeric accusations."""

import pytest
from sqlalchemy import func, select

from app import presentation as p
from app.i18n import t, use_language
from app.models import BanAction
from app.services import Service


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_known_id_matching_unresolved_record_warns_without_binding(database, settings, lang):
    async with database() as session:
        core = Service(settings, session)
        historical = await core.add_scam(900, "@historicalname", "Historical incident reason")
        await core.observe(41, "historicalname", "Current owner")
        result = await core.profile("41")
        assert result["scam"] is None and result["unresolved_scam"].id == historical.id
        assert not result["trusted"]
        assert historical.target.telegram_id is None
        with use_language(lang):
            text = p.profile(result)
            assert t("p.lookup_username_match") in text
            assert t("p.lookup_clear_status") not in text
            assert t("p.lookup_scam_status") not in text
            assert t("p.lookup_username_match_description") in text
            assert "🆔 ID: <code>41</code>" in text
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0
        await core.observe(41, "renamedowner", "Current owner")
        renamed = await core.profile("41")
        assert renamed["unresolved_scam"] is None and renamed["scam"] is None
        with use_language(lang):
            assert t("p.lookup_clear_status") in p.profile(renamed)
        confirmed = await core.add_scam(900, "41")
        await core.observe(41, "historicalname", "Current owner")
        current = await core.profile("41")
        assert current["scam"].id == confirmed.id and current["unresolved_scam"] is None
        with use_language(lang):
            assert t("p.lookup_scam_status") in p.profile(current)
            assert t("p.lookup_username_match") not in p.profile(current)

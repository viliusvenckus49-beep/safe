"""Native multi-group navigation, durable selections and author-bound confirmations."""

import pytest
from aiogram.methods import EditMessageText
from sqlalchemy import select
from test_ads_global import global_clock as global_clock
from test_ads_global import setup
from test_ads_publication import clock as clock
from test_ads_publication_ui import members as members
from test_telegram import journey as journey

from app.bot.global_ads import base36, cb
from app.i18n import CATALOGS, t
from app.models import AdGlobalOperation, AdSchedule
from app.services import Service


def latest(journey):
    return [c for c in journey.transport.calls if isinstance(c, EditMessageText)][-1]


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_global_selection_confirmation_and_individual_controls(
    journey, database, settings, clock, members, lang
):
    from app.bot.auto_ads import cb as unified_cb

    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.click(cb("root"), actor=900)
    assert "AUTO ADS" in latest(journey).text and "GLOBAL ADS" not in latest(journey).text
    await journey.click(unified_cb("select_groups", cid), actor=900)
    state = await journey.data(900)
    version = state["ua_group_version"]
    await journey.click(unified_cb("toggle_group", cid, version, "-200.0"), actor=900)
    await journey.click(unified_cb("apply_groups", cid, version), actor=900)
    async with database() as session:
        schedules = list(
            await session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid))
        )
        assert {r.chat_id for r in schedules} == {-100, -200}
    await journey.click(unified_cb("interval", cid), actor=900)
    payload = next(
        b.callback_data
        for row in latest(journey).reply_markup.inline_keyboard
        for b in row
        if b.text == t("ua.hour", lang, n=2)
    )
    await journey.click(payload, actor=900)
    async with database() as session:
        schedules = list(
            await session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid))
        )
        assert all(r.interval_seconds == 7200 for r in schedules if not r.excluded)


@pytest.mark.parametrize(
    "action",
    [
        "root",
        "list",
        "campaign",
        "groups",
        "mode",
        "toggle",
        "ready",
        "confirm",
        "bulk",
        "defaults",
        "dv",
        "de",
        "remove",
        "inherit",
        "future",
        "stats",
        "history",
        "managed",
        "chat",
        "progress",
    ],
)
async def test_nonadmin_cannot_forge_global_callbacks(journey, database, settings, clock, action):
    cid, sid, _ = await setup(database, settings, clock)
    async with database() as session:
        version = (await session.get(AdSchedule, sid)).version
    await journey.click(cb(action, cid, "1"), actor=1)
    async with database() as session:
        assert (await session.get(AdSchedule, sid)).version == version


def test_global_locale_keys_and_callback_byte_limits():
    keys = {key for key in CATALOGS["lt"] if key.startswith("g.")}
    assert keys and all(
        keys == {key for key in CATALOGS[lang] if key.startswith("g.")} for lang in ["en", "ru"]
    )
    value = ".".join(["f" * 16, base36(2**31 - 1), base36(-(2**63 - 1)), base36(10000)])
    assert len(cb("toggle", 2**31 - 1, value).encode()) <= 64
    assert int(base36(-(2**63 - 1)), 36) == -(2**63 - 1)


@pytest.mark.parametrize("action", ["toggle", "ready", "select", "confirm"])
async def test_truncated_owner_callbacks_are_handled(
    journey, database, settings, clock, members, action
):
    from app.ads_global import GlobalAdsService

    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        op = await GlobalAdsService(settings, session).selection(900, cid, "SELECTED", "truncated")
        oid = op.id
    await journey.click(cb(action, cid, oid), actor=900)
    async with database() as session:
        assert (await session.get(AdGlobalOperation, oid)).status == "SELECTING"


async def test_selection_is_recoverable_without_fsm(journey, database, settings, clock, members):
    from app.ads_global import GlobalAdsService

    cid, _, bot = await setup(database, settings, clock)
    async with database() as session:
        service = GlobalAdsService(settings, session)
        op = await service.selection(900, cid, "SELECTED", "recover-ui")
        op = await service.toggle(900, op.id, op.version, -200, bot)
        oid = op.id
    await journey.click(cb("resume", cid, oid), actor=900)
    assert "CRIMSON SAFECHECK" in latest(journey).text
    async with database() as session:
        # Cached selection data is retained, but never silently applied by navigation.
        op = await session.get(AdGlobalOperation, oid)
        assert op.status == "SELECTING" and op.payload["selected"] == [-200]
        assert (
            len(
                list(await session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid)))
            )
            == 1
        )

"""Native multi-group navigation, durable selections and author-bound confirmations."""

import pytest
from aiogram.methods import EditMessageText
from sqlalchemy import select
from test_ads_global import global_clock as global_clock
from test_ads_global import setup
from test_ads_publication import clock as clock
from test_ads_publication_ui import members as members
from test_telegram import journey as journey

from app.bot.ads_schedule import cb as schedule_cb
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
    cid, _, _ = await setup(database, settings, clock)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.click(cb("root"), actor=900)
    assert t("g.title", lang) in latest(journey).text
    await journey.click(cb("campaign", cid), actor=900)
    assert "&lt;secret&gt;" in latest(journey).text
    await journey.click(cb("groups", cid), actor=900)
    await journey.click(cb("mode", cid, "SELECTED"), actor=900)
    async with database() as session:
        op = await session.scalar(select(AdGlobalOperation))
        oid, version = op.id, op.version
    for chat in [-100, -200]:
        await journey.click(
            cb("toggle", cid, ".".join([oid, base36(version), base36(chat), "0"])), actor=900
        )
        version += 1
    assert "☑" in str(latest(journey).reply_markup)
    await journey.click(cb("ready", cid, oid + "." + base36(version)), actor=900)
    confirmation = latest(journey).reply_markup.inline_keyboard[0][0].callback_data
    await journey.click(confirmation, actor=900)
    await journey.click(confirmation, actor=900)
    async with database() as session:
        schedules = list(
            await session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid))
        )
        assert len(schedules) == 2
        target = next(r for r in schedules if r.chat_id == -200)
        sid, version = target.id, target.version
    await journey.click(schedule_cb("panel", cid, sid=sid), actor=900)
    assert t("g.remove", lang) in str(latest(journey).reply_markup)
    await journey.click(schedule_cb("intervals", cid, sid=sid), actor=900)
    payload = latest(journey).reply_markup.inline_keyboard[1][0].callback_data
    await journey.click(payload, actor=900)
    assert t("g.defaults_confirm", lang) in latest(journey).text
    async with database() as session:
        assert (await session.get(AdSchedule, sid)).version == version
    await journey.click(latest(journey).reply_markup.inline_keyboard[0][0].callback_data, actor=900)
    async with database() as session:
        assert (await session.get(AdSchedule, sid)).interval_seconds == 7200


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
    await journey.click(cb("operations"), actor=900)
    await journey.click(cb("resume", cid, oid), actor=900)
    assert "☑" in str(latest(journey).reply_markup)

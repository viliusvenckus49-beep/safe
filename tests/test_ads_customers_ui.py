"""Native aiogram journeys; API transport doubles do not establish live delivery."""

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMediaGroup, SendMessage
from aiogram.types import MessageEntity
from sqlalchemy import func, select
from test_ads_customers import link, setup, stamp
from test_telegram import journey as journey

from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_services import AdsService
from app.bot.ads import cb as admin_cb
from app.bot.ads import customer_cb
from app.bot.callbacks import AdPublication, CustomerAdmin, GlobalAd
from app.i18n import t
from app.models import AdCampaign, AdContentDraft, AdContentVersion
from app.services import Service


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_start_link_and_customer_shared_editor_native_entities(
    journey, database, settings, lang
):
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await journey.send("/start ad_" + token, actor=42)
    assert t("ac.title", lang) in journey.text()
    assert "ADMIN_INTERNAL_ONLY" in journey.text()
    before = len(journey.transport.calls)
    await journey.click(customer_cb("edit", cid, "TEXT"), actor=42)
    await journey.send(
        "😀 Native <b>text</b>",
        actor=42,
        entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="12345")],
    )
    async with database() as session:
        did = (await session.scalar(select(AdContentDraft))).id
    await journey.click(customer_cb("draft_preview", cid, did), actor=42)
    preview = [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage) and c.text == "😀 Native <b>text</b>"
    ][-1]
    assert preview.parse_mode is None and preview.entities[0].custom_emoji_id == "12345"
    await journey.click(customer_cb("save", cid, did), actor=42)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2
        assert (await CustomerAdsService(settings, session).current(42, cid))[1].actor_id == 42
    assert t("ads.saved", lang, version=2) in journey.text()
    # Editing updates a single existing control panel; preview itself is a separate ad.
    panels = [c for c in journey.transport.calls[before:] if isinstance(c, EditMessageText)]
    assert len({c.message_id for c in panels}) == 1


async def test_new_customer_without_language_still_opens_start_payload(journey, database, settings):
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    await journey.send("/start ad_" + token, actor=42)
    assert t("ac.title") in journey.text()
    await journey.send("/start ad_" + token, actor=43)
    assert t("ac.denied") in journey.text()
    await journey.send("/start ad_" + token, actor=42, chat=-100)
    assert journey.transport.calls[-1].text == t("ac.denied")


@pytest.mark.parametrize("route", ["create", "archive", "restore", "new", "rename"])
async def test_forged_customer_admin_action_is_rejected(journey, database, settings, route):
    cid, _ = await setup(database, settings)
    await journey.click(customer_cb(route, cid, "1"), actor=42)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).status == "DRAFT"
        assert (await session.get(AdCampaign, cid)).version == 1
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 1
    assert [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][-1].text == t(
        "ac.denied"
    )


@pytest.mark.parametrize("route", ["schedule", "global", "grant", "admin"])
async def test_customer_cannot_reach_admin_routes(journey, database, settings, route):
    cid, _ = await setup(database, settings)
    callback = {
        "schedule": AdPublication(action="panel", campaign=cid).pack(),
        "global": GlobalAd(action="campaign", campaign=cid).pack(),
        "grant": CustomerAdmin(action="panel", campaign=cid).pack(),
        "admin": admin_cb("edit", cid, "TEXT"),
    }[route]
    await journey.click(callback, actor=42)
    assert await journey.state(42) is None
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 1


async def test_old_fsm_and_draft_cannot_save_after_reassignment(journey, database, settings):
    cid, _ = await setup(database, settings)
    await journey.click(customer_cb("edit", cid, "TEXT"), actor=42)
    async with database() as session:
        access = CustomerAccess(settings, session)
        await access.assign(900, cid, 42, await stamp(access, cid))
    await journey.send("Old pending input", actor=42)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(AdContentDraft)) == 0
    await journey.click(customer_cb("edit", cid, "TEXT"), actor=42)
    await journey.send("Fresh input", actor=42)
    async with database() as session:
        did = (await session.scalar(select(AdContentDraft))).id
        access = CustomerAccess(settings, session)
        await access.revoke(900, cid, await stamp(access, cid))
    await journey.click(customer_cb("draft_preview", cid, did), actor=42)
    await journey.click(customer_cb("save", cid, did), actor=42)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 1


async def test_customer_album_buttons_popup_scope_and_restart(journey, database, settings):
    cid, _ = await setup(database, settings)
    await journey.click(customer_cb("edit", cid, "MEDIA"), actor=42)
    for index in range(2):
        await journey.send(
            actor=42,
            photo=[
                {
                    "file_id": f"photo_{index}",
                    "file_unique_id": f"p{index}",
                    "width": 100,
                    "height": 100,
                }
            ],
            media_group_id="one",
        )
    async with database() as session:
        did = (await session.scalar(select(AdContentDraft))).id
    await journey.click(customer_cb("finish", cid, did), actor=42)
    await journey.click(customer_cb("draft_preview", cid, did), actor=42)
    assert len([c for c in journey.transport.calls if isinstance(c, SendMediaGroup)][-1].media) == 2
    await journey.click(customer_cb("save", cid, did), actor=42)
    await journey.click(customer_cb("edit", cid, "BUTTONS"), actor=42)
    await journey.send(
        "😀 Open - popup: Hello",
        actor=42,
        entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="12345")],
    )
    async with database() as session:
        did = (
            await session.scalar(select(AdContentDraft).where(AdContentDraft.field == "BUTTONS"))
        ).id
    await journey.click(customer_cb("draft_preview", cid, did), actor=42)
    button = [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage)
        and c.reply_markup
        and c.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id == "12345"
    ][-1].reply_markup.inline_keyboard[0][0]
    assert button.callback_data.startswith("ab3:cd")
    await journey.click(button.callback_data, actor=43)
    assert [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][
        -1
    ].text != "Hello"
    await journey.click(button.callback_data, actor=42)
    assert [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][
        -1
    ].text == "Hello"
    await journey.click(customer_cb("save", cid, did), actor=42)
    async with database() as session:
        assert (
            len((await CustomerAdsService(settings, session).current(42, cid))[1].content["media"])
            == 2
        )


async def test_one_time_link_copy_and_numeric_assignment_admin_only(
    journey, database, settings, caplog
):
    from app.ads_invitations import ClientInvitations
    from app.bot.auto_ads import cb as unified_cb

    async with database() as session:
        cid = (await AdsService(settings, session).create(900, "Name", "seed")).id
    await journey.click(unified_cb("assign", cid, value="0.0"), actor=900)
    await journey.send("42", actor=900)
    async with database() as session:
        access = CustomerAccess(settings, session)
        grant_stamp = await stamp(access, cid)
    await journey.click(unified_cb("generate", cid, value=grant_stamp), actor=900)
    panel = [
        c
        for c in journey.transport.calls
        if isinstance(c, EditMessageText)
        and c.reply_markup
        and c.reply_markup.inline_keyboard[0][0].copy_text
    ][-1]
    url = panel.reply_markup.inline_keyboard[0][0].copy_text.text
    payload = url.split("start=", 1)[1]
    assert url.startswith("https://t.me/redsafecheckbot?start=ai_")
    assert payload not in caplog.text and payload not in str(await journey.data(900))
    async with database() as session:
        assert await ClientInvitations(settings, session).consume(42, payload) == cid
    await journey.click(unified_cb("links", cid), actor=900)
    assert payload not in journey.transport.calls[-1].text
    await journey.click(unified_cb("generate", cid, value=grant_stamp), actor=43)
    async with database() as session:
        assert (await CustomerAccess(settings, session).latest(cid)).customer_id == 42


async def test_list_pagination_home_visibility_stats_and_readonly_history(
    journey, database, settings
):
    cid, _ = await setup(database, settings)
    async with database() as session:
        for index in range(9):
            campaign = await AdsService(settings, session).create(
                900, f"Private {index}", f"other{index}"
            )
            await CustomerAccess(settings, session).assign(900, campaign.id, 42, "0.0")
        await Service(settings, session).set_language(42, "lt")
        await Service(settings, session).set_language(43, "lt")
    await journey.send("/start", actor=42)
    assert t("ac.mine") in repr(journey.transport.calls[-1].reply_markup)
    await journey.send("/start", actor=43)
    assert t("ac.mine") not in repr(journey.transport.calls[-1].reply_markup)
    await journey.send("/myads", actor=42)
    assert t("ads.next") in repr(journey.transport.calls[-1].reply_markup)
    assert "Private" not in journey.text()
    await journey.click(customer_cb("list", value="1"), actor=42)
    await journey.click(customer_cb("stats", cid), actor=42)
    assert (
        t("ac.stats", sent=0, failed=0, uncertain=0, partial=0, last="—", next="—")
        in journey.text()
    )
    await journey.click(customer_cb("history", cid), actor=42)
    assert "v1" in journey.text()
    assert "ac4:restore" not in repr(journey.transport.calls[-1].reply_markup)


async def test_admin_scope_is_reset_when_entering_global_creation(journey, database, settings):
    cid, _ = await setup(database, settings, customer=900)
    await journey.click(customer_cb("edit", cid, "TEXT"), actor=900)
    assert (await journey.data(900))["ads_customer"] is True
    await journey.click(GlobalAd(action="new").pack(), actor=900)
    await journey.send("Global creation after customer input", actor=900)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(AdCampaign)) == 2
        assert (
            await session.scalar(
                select(AdCampaign).where(
                    AdCampaign.internal_name == "Global creation after customer input"
                )
            )
            is not None
        )


async def test_real_redis_customer_fsm_restart_and_token_not_persisted(database, settings):
    import os
    from uuid import uuid4

    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
    from aiogram.fsm.storage.redis import RedisStorage
    from test_telegram import Journey, Transport

    from app.bot.handlers import create_router

    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for isolated Redis integration")
    cid, _ = await setup(database, settings)
    token = await link(database, settings, cid)
    keys = DefaultKeyBuilder(prefix="ads34_" + uuid4().hex)
    first, second = (
        RedisStorage.from_url(url, key_builder=keys),
        RedisStorage.from_url(url, key_builder=keys),
    )
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)

    def client(storage):
        dp = Dispatcher(storage=storage)
        dp.include_router(create_router(settings, database))
        return Journey(bot, dp, transport)

    key = StorageKey(bot_id=bot.id, chat_id=42, user_id=42)
    try:
        initial = client(first)
        await initial.send("/start ad_" + token, actor=42)
        assert await first.get_data(key) == {}
        await initial.click(customer_cb("edit", cid, "TEXT"), actor=42)
        snapshot = await first.get_data(key)
        assert token not in repr(snapshot) and snapshot["ads_customer"] is True
        restarted = client(second)
        assert await restarted.state(42) is not None
        await restarted.send("Survives Redis client restart", actor=42)
        async with database() as session:
            did = (await session.scalar(select(AdContentDraft))).id
            access = CustomerAccess(settings, session)
            await access.revoke(900, cid, await stamp(access, cid))
        await restarted.click(customer_cb("save", cid, did), actor=42)
        async with database() as session:
            assert (await session.get(AdCampaign, cid)).version == 1
            assert not (await session.get(AdContentDraft, did)).previewed
    finally:
        await second.redis.delete(keys.build(key, "state"), keys.build(key, "data"))
        await first.close()
        await second.close()
        await bot.session.close()

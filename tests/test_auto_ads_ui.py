"""Native aiogram/FSM journeys with a simulated Bot API transport (not live Telegram)."""

import pytest
from aiogram.methods import EditMessageText, SendAnimation, SendDocument, SendMessage
from sqlalchemy import select
from test_auto_ads_unified import assign, create
from test_telegram import journey as journey

from app.ads_customers import CustomerAccess
from app.ads_services import AdsService
from app.bot.ads import cb as old_cb
from app.bot.auto_ads import cb
from app.bot.callbacks import AdPublication, GlobalAd
from app.bot.keyboards import action
from app.errors import DomainError
from app.i18n import CATALOGS, t
from app.models import AdCampaign, AdContentDraft
from app.services import Service


def last_screen(journey):
    return [c for c in journey.transport.calls if isinstance(c, (SendMessage, EditMessageText))][-1]


def buttons(journey):
    markup = last_screen(journey).reply_markup
    return [button for row in markup.inline_keyboard for button in row] if markup else []


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_root_list_and_individual_ui_have_no_duplicate_navigation(
    journey, database, settings, lang
):
    cid = await create(database, settings, "Partner <private>")
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.send("/ads", actor=900)
    assert "AUTO ADS" in last_screen(journey).text
    assert t("ua.mine", lang) in [b.text for b in buttons(journey)]
    assert action("group_help") in [b.callback_data for b in buttons(journey)]
    await journey.click(cb("list"), actor=900)
    assert "Partner &lt;private&gt;" in last_screen(journey).text
    await journey.click(cb("panel", cid), actor=900)
    labels = [b.text for b in buttons(journey)]
    for key in (
        "text",
        "media",
        "buttons",
        "interval",
        "groups",
        "client",
        "link",
        "validity",
        "stats",
        "history",
        "preview",
        "resume",
        "delete",
    ):
        assert t("ua." + key, lang) in labels
    assert not any(
        term in last_screen(journey).text + str(labels)
        for term in ("GLOBAL ADS", "Global ADS", "Campaigns", "Recurring Messages")
    )
    # Native management transitions edit the same panel; no decorative web controls.
    edits = [c for c in journey.transport.calls if isinstance(c, EditMessageText)]
    assert len({c.message_id for c in edits}) == 1


async def test_creation_name_to_persisted_editor_and_draft(journey, database, settings):
    await journey.send("/ads", actor=900)
    await journey.click(old_cb("new"), actor=900)
    await journey.send("VIP REKLAMA", actor=900)
    async with database() as session:
        campaign = await session.scalar(select(AdCampaign))
        assert campaign.internal_name == "VIP REKLAMA"
        cid = campaign.id
    assert "VIP REKLAMA" in last_screen(journey).text
    await journey.click(cb("draft", cid), actor=900)
    assert t("ua.saved_draft") in last_screen(journey).text


async def test_search_pagination_filters_use_real_data(journey, database, settings):
    for index in range(6):
        await create(database, settings, f"AD {index}", f"create-{index}")
    await journey.send("/ads", actor=900)
    await journey.click(cb("list"), actor=900)
    ad_buttons = [b for b in buttons(journey) if b.text.startswith("AD ")]
    assert len(ad_buttons) == 4
    await journey.click(cb("list", value=1), actor=900)
    assert len([b for b in buttons(journey) if b.text.startswith("AD ")]) == 2
    await journey.click(cb("search"), actor=900)
    await journey.send("AD 3", actor=900)
    assert [b.text for b in buttons(journey) if b.text.startswith("AD ")] == ["AD 3"]
    await journey.click(cb("filter", value="active"), actor=900)
    assert t("ua.empty") in last_screen(journey).text


async def test_url_crud_order_uses_isolated_drafts_and_preview_save(journey, database, settings):
    cid = await create(database, settings)
    await journey.click(cb("buttons", cid), actor=900)
    await journey.click(cb("b_add", cid, 2), actor=900)
    await journey.send("Partner - https://example.com", actor=900)
    async with database() as session:
        draft = await session.scalar(
            select(AdContentDraft).where(AdContentDraft.field == "BUTTONS")
        )
        assert draft.content["buttons"][0][0]["value"] == "https://example.com"
        did = draft.id
        assert (await session.get(AdCampaign, cid)).version == 2
    await journey.click(old_cb("draft_preview", cid, did), actor=900)
    assert any(
        c.reply_markup
        and any(b.url == "https://example.com" for r in c.reply_markup.inline_keyboard for b in r)
        for c in journey.transport.calls
        if isinstance(c, SendMessage)
    )
    await journey.click(old_cb("save", cid, did), actor=900)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 3
    await journey.click(cb("b_edit", cid, 3, 0), actor=900)
    await journey.send("Changed - https://example.org", actor=900)
    async with database() as session:
        drafts = list(
            await session.scalars(
                select(AdContentDraft)
                .where(AdContentDraft.field == "BUTTONS")
                .order_by(AdContentDraft.created_at)
            )
        )
        assert drafts[-1].content["buttons"][0][0]["label"] == "Changed"
    await journey.click(cb("b_remove", cid, 3, 0), actor=900)
    async with database() as session:
        drafts = list(
            await session.scalars(
                select(AdContentDraft)
                .where(AdContentDraft.field == "BUTTONS")
                .order_by(AdContentDraft.created_at)
            )
        )
        assert drafts[-1].content["buttons"] == []


async def test_legacy_management_callbacks_redirect_to_auto_ads(journey, database, settings):
    cid = await create(database, settings)
    for data in (
        GlobalAd(action="root").pack(),
        GlobalAd(action="campaign", campaign=cid).pack(),
        GlobalAd(action="mode", campaign=cid, value="FUTURE").pack(),
        AdPublication(action="panel", campaign=cid).pack(),
    ):
        await journey.click(data, actor=900)
        assert "GLOBAL ADS" not in last_screen(journey).text
        assert "CRIMSON SAFECHECK" in last_screen(journey).text
    await journey.click(cb("panel", cid), actor=42)
    assert t("ads.permission") in journey.text() or t("ads.permission") in str(
        journey.transport.calls[-1]
    )


async def test_link_binding_and_client_editor_are_grant_authorized(journey, database, settings):
    cid = await create(database, settings)
    await journey.click(cb("generate", cid, value="0.0"), actor=900)
    copy = next(b for b in buttons(journey) if b.copy_text)
    link = copy.copy_text.text
    assert link.startswith("https://t.me/redsafecheckbot?start=ai_")
    await journey.send("/start " + link.split("start=")[1], actor=42)
    assert t("ua.client_editor") in last_screen(journey).text
    assert "REDSAFE PARTNER" in last_screen(journey).text
    forbidden_labels = [
        t("ua." + key) for key in ("interval", "groups", "validity", "client", "delete", "resume")
    ]
    assert not any(b.text in forbidden_labels for b in buttons(journey))
    await journey.click(cb("links", cid), actor=900)
    permanent = next(b.copy_text.text for b in buttons(journey) if b.copy_text)
    await journey.send("/start " + permanent.split("start=")[1], actor=42)
    assert t("ua.client_editor") in last_screen(journey).text
    await journey.send("/start " + permanent.split("start=")[1], actor=43)
    assert t("ua.expired_link") in last_screen(journey).text
    await journey.click(cb("interval", cid), actor=42)
    async with database() as session:
        grant = await CustomerAccess(settings, session).check(42, cid)
        assert grant.customer_id == 42


async def test_delete_confirmation_back_and_stale_confirm(journey, database, settings):
    cid = await create(database, settings)
    await journey.click(cb("panel", cid), actor=900)
    delete = next(b.callback_data for b in buttons(journey) if b.text == t("ua.delete"))
    await journey.click(delete, actor=900)
    confirm = next(b.callback_data for b in buttons(journey) if b.text == t("ua.confirm"))
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).status == "DRAFT"
    await journey.click(confirm, actor=900)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).status == "ARCHIVED"
    await journey.click(confirm, actor=900)
    assert t("ads.missing") in str(journey.transport.calls[-1])


@pytest.mark.parametrize("kind", ["animation", "document"])
async def test_native_media_preview_does_not_publish_to_a_group(journey, database, settings, kind):
    cid = await create(database, settings)
    async with database() as session:
        ads = AdsService(settings, session)
        draft = await ads.stage(
            900, cid, 2, "MEDIA", [{"kind": kind, "file_id": "telegram_file"}], kind
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
    await journey.click(old_cb("preview", cid, "FULL"), actor=900)
    cls = SendAnimation if kind == "animation" else SendDocument
    call = next(c for c in journey.transport.calls if isinstance(c, cls))
    assert call.chat_id == 900 and call.caption == "REDSAFE PARTNER"


def test_locale_keys_and_format_placeholders_have_parity():
    from string import Formatter

    keys = {k for k in CATALOGS["lt"] if k.startswith("ua.")}
    for lang in ("en", "ru"):
        assert keys == {k for k in CATALOGS[lang] if k.startswith("ua.")}
        for key in keys:

            def placeholders(value):
                return {name for _, name, _, _ in Formatter().parse(value) if name}

            assert placeholders(CATALOGS[lang][key]) == placeholders(CATALOGS["lt"][key])


async def test_real_redis_unified_search_resumes_after_client_restart(database, settings):
    import os
    from uuid import uuid4

    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
    from aiogram.fsm.storage.redis import RedisStorage
    from test_telegram import Journey, Transport

    from app.bot.handlers import create_router

    redis_url = os.getenv("TEST_REDIS_URL")
    if not redis_url:
        pytest.skip("Set TEST_REDIS_URL for isolated real Redis checks")
    await create(database, settings, "RED PARTNER", "red")
    await create(database, settings, "BLUE VIP", "blue")
    keys = DefaultKeyBuilder(prefix="auto_ads_" + uuid4().hex)
    first = RedisStorage.from_url(redis_url, key_builder=keys)
    second = RedisStorage.from_url(redis_url, key_builder=keys)
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)

    def client(storage):
        dispatcher = Dispatcher(storage=storage)
        dispatcher.include_router(create_router(settings, database))
        return Journey(bot, dispatcher, transport)

    key = StorageKey(bot_id=bot.id, chat_id=900, user_id=900)
    try:
        initial = client(first)
        await initial.click(cb("search"), actor=900)
        assert (await first.get_data(key))["ua_field"] == "search"
        restarted = client(second)
        assert await restarted.state(900) is not None
        await restarted.send("BLUE", actor=900)
        assert "BLUE VIP" in last_screen(restarted).text
        assert "RED PARTNER" not in last_screen(restarted).text
    finally:
        await second.redis.delete(keys.build(key, "state"), keys.build(key, "data"))
        await first.close()
        await second.close()
        await bot.session.close()


async def test_private_draft_recovery_is_scoped_to_selected_ad(journey, database, settings):
    a = await create(database, settings, "A", "a")
    b = await create(database, settings, "B", "b")
    async with database() as session:
        ads = AdsService(settings, session)
        drafts = []
        for cid in (a, b):
            drafts.append(
                await ads.stage(
                    900, cid, 2, "TEXT", {"text": "Pending", "entities": []}, f"pending-{cid}"
                )
            )
    await journey.click(cb("panel", a), actor=900)
    assert t("ads.drafts") in [button.text for button in buttons(journey)]
    await journey.click(cb("drafts", a), actor=900)
    callbacks = [button.callback_data for button in buttons(journey)]
    assert old_cb("draft", a, drafts[0].id) in callbacks
    assert not any(drafts[1].id in payload for payload in callbacks)


async def test_unlink_disables_selector_until_admin_generates_new_link(journey, database, settings):
    from app.ads_invitations import ClientInvitations

    cid = await create(database, settings)
    grant = await assign(database, settings, cid)
    await journey.click(cb("generate", cid, value=f"{grant.id}.{grant.version}"), actor=900)
    await journey.click(cb("links", cid), actor=900)
    permanent = next(button.copy_text.text for button in buttons(journey) if button.copy_text)
    await journey.click(cb("unlink", cid, value=f"{grant.id}.{grant.version}"), actor=900)
    assert not any(button.copy_text for button in buttons(journey))
    async with database() as session:
        links = ClientInvitations(settings, session)
        with pytest.raises(DomainError):
            await links.editor(42, permanent.split("start=")[1])
        current = await links.grants.check(42, cid)
        with pytest.raises(DomainError):
            await links.editor(42, f"ae_{cid}_{current.id}_{current.version}")


async def test_client_editing_deadline_is_separate_from_publication_expiry(
    journey, database, settings
):
    from app.ads_customers import CustomerAccess
    from app.moderation_services import utc

    cid = await create(database, settings)
    grant = await assign(database, settings, cid)
    await journey.click(cb("client_date", cid, value=f"{grant.id}.{grant.version}"), actor=900)
    await journey.send("2026-11-20 12:00", actor=900)
    async with database() as session:
        access = CustomerAccess(settings, session)
        current = await access.latest(cid)
        assert utc(current.editing_until).isoformat() == "2026-11-20T10:00:00+00:00"
        from app.models import AdAssignmentPolicy

        policy = await session.get(AdAssignmentPolicy, cid)
        assert policy.end_at is None

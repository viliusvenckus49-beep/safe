"""Bottom-controller native journeys. Telegram transport is simulated, not live delivery."""

from copy import deepcopy
from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMediaGroup, SendMessage
from aiogram.types import PhotoSize
from sqlalchemy import func, select
from test_auto_ads_unified import assign, create
from test_integration_contracts import postgres_contract as postgres_contract
from test_telegram import journey as journey

from app.ads_customers import CustomerAccess
from app.ads_services import AdsService
from app.bot.ads import cb, customer_cb
from app.bot.auto_ads import cb as menu_cb
from app.i18n import t
from app.models import AdCampaign, AdContentDraft, AdContentVersion, AuditEvent
from app.services import Service


async def data(journey, actor=900):
    from aiogram.fsm.storage.base import StorageKey

    key = StorageKey(bot_id=journey.bot.id, chat_id=actor, user_id=actor)
    return await journey.dp.storage.get_data(key)


async def click(journey, action, cid, value=None, *, actor=900, customer=False):
    current = await data(journey, actor)
    return await journey.click(
        (customer_cb if customer else cb)(
            action, cid, value if value is not None else current["q_token"]
        ),
        actor=actor,
        message_id=current.get("q_panel") or 100,
    )


async def setup_editor(journey, database, settings, *, customer=False):
    cid = await create(database, settings)
    if customer:
        await assign(database, settings, cid)
    return cid, 42 if customer else 900


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("customer", [False, True])
@pytest.mark.parametrize("field", ["TEXT", "MEDIA", "BUTTONS"])
async def test_input_saves_immediately_with_one_bottom_receipt_and_back(
    journey, database, settings, customer, lang, field
):
    cid, actor = await setup_editor(journey, database, settings, customer=customer)
    async with database() as session:
        await Service(settings, session).set_language(actor, lang)
        before = deepcopy((await AdsService(settings, session).current(900, cid))[1].content)
    await click(journey, "qe", cid, f"{field}.2", actor=actor, customer=customer)
    prompt = await data(journey, actor)
    assert t("ads.send_now", lang) in journey.text()
    if field == "TEXT":
        await journey.send("New native text", actor=actor)
    elif field == "MEDIA":
        await journey.send(
            actor=actor,
            photo=[PhotoSize(file_id="photo_one", file_unique_id="unique", width=1, height=1)],
        )
    else:
        await journey.send("Open - https://example.com", actor=actor)
    saved = await data(journey, actor)
    assert saved["q_kind"] == "SET" and saved["q_panel"] != prompt["q_panel"]
    assert any(m.message_id == prompt["q_panel"] for m in journey.transport.deletions)
    assert t("ads.message_set", lang) in journey.text()
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 3
        assert version.actor_id == actor
        draft = await session.scalar(
            select(AdContentDraft).where(AdContentDraft.saved_revision == 3)
        )
        assert draft.status == "SAVED" and not draft.previewed
        event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.action == "ads_content_saved")
            .order_by(AuditEvent.id.desc())
        )
        assert event.details["validation"] == "native_input"
        for key in ("text", "media", "buttons"):
            if key != field.lower():
                assert version.content[key] == before[key]
    receipt_id = saved["q_panel"]
    await click(journey, "qb", cid, actor=actor, customer=customer)
    current = await data(journey, actor)
    assert current["q_kind"] == "MENU" and current["q_panel"] != receipt_id
    assert any(m.message_id == receipt_id for m in journey.transport.deletions)
    assert not any(
        t("ads.save", lang) == b.text
        for row in journey.transport.calls[-1].reply_markup.inline_keyboard
        for b in row
    )
    assert all(
        getattr(c, "chat_id", actor) == actor
        for c in journey.transport.calls
        if isinstance(c, (SendMessage, SendMediaGroup))
    )


async def test_repeated_preview_and_back_delete_prior_preview_and_keep_controls_below(
    journey, database, settings
):
    cid = await create(database, settings)
    for _ in range(3):
        await click(journey, "qp", cid, "FULL")
        frame = await data(journey)
        assert frame["q_kind"] == "PREVIEW" and len(frame["q_previews"]) == 1
        last = journey.transport.calls[-1]
        assert isinstance(last, SendMessage) and last.text != "REDSAFE PARTNER"
        preview = frame["q_previews"][0]
        old_frame = frame["q_panel"]
        await click(journey, "qb", cid)
        deleted = {m.message_id for m in journey.transport.deletions}
        assert preview in deleted and old_frame in deleted
        assert (await data(journey))["q_previews"] == []


async def test_stale_back_and_clear_cannot_erase_newer_content(journey, database, settings):
    cid = await create(database, settings)
    await click(journey, "qe", cid, "TEXT.2")
    old = await data(journey)
    await journey.send("Saved", actor=900)
    for action in ("qr", "qb", "qf"):
        await journey.click(cb(action, cid, old["q_token"]), actor=900, message_id=old["q_panel"])
        assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        assert journey.transport.calls[-1].show_alert
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 3 and version.content["text"] == "Saved"


async def test_album_stays_atomic_until_finished_and_is_saved_once(journey, database, settings):
    cid = await create(database, settings)
    await click(journey, "qe", cid, "MEDIA.2")
    for index in range(2):
        await journey.send(
            actor=900,
            media_group_id="same-album",
            photo=[
                PhotoSize(
                    file_id="photo_" + str(index),
                    file_unique_id="file" + str(index),
                    width=1,
                    height=1,
                )
            ],
        )
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2
    old = await data(journey)
    await click(journey, "qf", cid)
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 3 and len(version.content["media"]) == 2
        assert version.content["text"] == "REDSAFE PARTNER"
    await journey.click(cb("qf", cid, old["q_token"]), actor=900, message_id=old["q_panel"])
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 3


async def test_invalid_input_receipt_is_bottom_and_previous_content_is_retained(
    journey, database, settings
):
    cid = await create(database, settings)
    await click(journey, "qe", cid, "BUTTONS.2")
    old = await data(journey)
    await journey.send("Open - javascript:bad", actor=900)
    current = await data(journey)
    assert current["q_kind"] == "INPUT" and current["q_panel"] != old["q_panel"]
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2
    await journey.send("Open - https://example.org", actor=900)
    assert (await data(journey))["q_kind"] == "SET"


async def test_client_revocation_during_input_is_checked_again(journey, database, settings):
    cid, actor = await setup_editor(journey, database, settings, customer=True)
    await click(journey, "qe", cid, "TEXT.2", actor=actor, customer=True)
    async with database() as session:
        access = CustomerAccess(settings, session)
        grant = await access.latest(cid)
        await access.revoke(900, cid, access.stamp(grant))
    await journey.send("Must not save", actor=actor)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2


async def test_legacy_save_still_requires_preview_but_received_save_is_idempotent(
    database, settings
):
    from app.errors import DomainError

    cid = await create(database, settings)
    async with database() as session:
        ads = AdsService(settings, session)
        draft = await ads.stage(900, cid, 2, "TEXT", {"text": "New", "entities": []}, "received")
        did = draft.id
        with pytest.raises(DomainError, match="ads_preview_required"):
            await ads.save(900, did)
        saved = await ads.save_received(900, did)
        same = await ads.save_received(900, did)
        assert saved.id == same.id
        assert not (await session.get(AdContentDraft, did)).previewed
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 3


async def test_deleted_message_cleanup_falls_back_to_disabling_old_keyboard(
    journey, database, settings
):
    from aiogram.methods import DeleteMessage

    cid = await create(database, settings)
    original = journey.transport.make_request

    async def fail_delete(bot, method, timeout=None):
        if isinstance(method, DeleteMessage):
            raise TelegramBadRequest(method=method, message="message can't be deleted")
        return await original(bot, method, timeout)

    journey.transport.make_request = fail_delete
    await click(journey, "qe", cid, "TEXT.2")
    await journey.send("New", actor=900)
    assert any(
        isinstance(c, EditMessageReplyMarkup) and c.reply_markup is None
        for c in journey.transport.calls
    )
    assert (await data(journey))["q_kind"] == "SET"


async def test_full_menu_buttons_are_connected_to_immediate_editor(journey, database, settings):
    cid = await create(database, settings)
    await journey.click(menu_cb("panel", cid), actor=900)
    rows = journey.transport.calls[-1].reply_markup.inline_keyboard
    text = next(b for row in rows for b in row if b.text == t("ua.text"))
    assert text.callback_data == cb("qe", cid, "TEXT.2")
    await journey.click(text.callback_data, actor=900)
    await journey.send("Saved immediately", actor=900)
    assert (await data(journey))["q_kind"] == "SET"


@pytest.mark.parametrize("customer", [False, True])
async def test_individual_url_add_edit_order_remove_use_immediate_save(
    journey, database, settings, customer
):
    cid, actor = await setup_editor(journey, database, settings, customer=customer)
    await click(journey, "qe", cid, "ADD.2", actor=actor, customer=customer)
    assert all(
        b.text != t("ads.clear")
        for row in journey.transport.calls[-1].reply_markup.inline_keyboard
        for b in row
    )
    await journey.send("One - https://one.example", actor=actor)
    await click(journey, "qb", cid, actor=actor, customer=customer)
    await click(journey, "qe", cid, "ADD.3", actor=actor, customer=customer)
    await journey.send("Two - https://two.example", actor=actor)
    await click(journey, "qb", cid, actor=actor, customer=customer)
    await click(journey, "qe", cid, "EDIT.4.0", actor=actor, customer=customer)
    await journey.send("Changed - https://changed.example", actor=actor)
    await click(journey, "qb", cid, actor=actor, customer=customer)
    await click(journey, "qk", cid, "ORDER.5.1", actor=actor, customer=customer)
    await click(journey, "qb", cid, actor=actor, customer=customer)
    await click(journey, "qk", cid, "REMOVE.6.0", actor=actor, customer=customer)
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 7 and version.content["buttons"][0][0]["label"] == "Changed"
    assert (await data(journey, actor))["q_kind"] == "SET"


async def test_preview_failure_retains_saved_content_and_has_bottom_back(
    journey, database, settings
):
    cid = await create(database, settings)
    old = journey.transport.make_request

    async def fail_preview(bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.text == "REDSAFE PARTNER":
            raise TelegramBadRequest(method=method, message="custom emoji unavailable")
        return await old(bot, method, timeout)

    journey.transport.make_request = fail_preview
    await click(journey, "qp", cid, "FULL")
    assert t("ads.preview_failed") in journey.text()
    assert (await data(journey))["q_panel"] > 0
    await click(journey, "qb", cid)
    assert (await data(journey))["q_kind"] == "MENU"
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2


async def test_receipt_delivery_failure_does_not_claim_save_failed_or_repeat_save(
    journey, database, settings
):
    cid = await create(database, settings)
    await click(journey, "qe", cid, "TEXT.2")
    old = journey.transport.make_request

    async def fail_receipt(bot, method, timeout=None):
        if isinstance(method, SendMessage) and t("ads.message_set") in method.text:
            raise TimeoutError("uncertain response")
        return await old(bot, method, timeout)

    journey.transport.make_request = fail_receipt
    await journey.send("Committed", actor=900)
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 3 and version.content["text"] == "Committed"
    assert t("ads.telegram") not in journey.text()


async def test_back_cancels_unfinished_album_without_saving_partial_media(
    journey, database, settings
):
    cid = await create(database, settings)
    await click(journey, "qe", cid, "MEDIA.2")
    await journey.send(
        actor=900,
        media_group_id="album",
        photo=[PhotoSize(file_id="photo_one", file_unique_id="unique", width=1, height=1)],
    )
    current = await data(journey)
    await click(journey, "qb", cid)
    async with database() as session:
        assert (await session.get(AdContentDraft, current["q_draft"])).status == "CANCELLED"
        assert (await session.get(AdCampaign, cid)).version == 2


async def test_unauthorized_and_foreign_ad_callbacks_never_write(journey, database, settings):
    cid = await create(database, settings)
    await assign(database, settings, cid, 42)
    for actor, customer in ((43, True), (42, False)):
        await journey.click((customer_cb if customer else cb)("qe", cid, "TEXT.2"), actor=actor)
        assert isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        assert journey.transport.calls[-1].show_alert
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2


async def test_edit_does_not_change_other_ad_or_schedule_or_reactivate_paused_ad(
    database, settings
):
    from app.models import AdSchedule, ManagedGroup, now

    first = await create(database, settings)
    second = await create(database, settings, "Other", "other")
    instant = now()
    async with database() as session:
        session.add(ManagedGroup(chat_id=-100, title="Test"))
        await session.flush()
        schedule = AdSchedule(
            campaign_id=first,
            chat_id=-100,
            status="PAUSED",
            authorized_by=900,
            interval_seconds=1800,
            start_at=instant,
            next_run_at=instant + timedelta(minutes=30),
        )
        session.add(schedule)
        await session.commit()
        sid = schedule.id
        before = (await AdsService(settings, session).current(900, second))[1].content
        ads = AdsService(settings, session)
        draft = await ads.stage(
            900, first, 2, "TEXT", {"text": "New", "entities": []}, "received-isolation"
        )
        await ads.save_received(900, draft.id)
        same = await session.get(AdSchedule, sid, populate_existing=True)
        assert same.status == "PAUSED" and same.next_run_at.replace(
            tzinfo=instant.tzinfo
        ) == instant + timedelta(minutes=30)
        assert (await ads.current(900, second))[1].content == before


@pytest.mark.parametrize("customer", [False, True])
async def test_clear_is_immediate_and_stale_original_clear_cannot_run_again(
    journey, database, settings, customer
):
    cid, actor = await setup_editor(journey, database, settings, customer=customer)
    await click(journey, "qe", cid, "TEXT.2", actor=actor, customer=customer)
    old = await data(journey, actor)
    await click(journey, "qr", cid, actor=actor, customer=customer)
    async with database() as session:
        campaign, version = await AdsService(settings, session).current(900, cid)
        assert campaign.version == 3 and version.content["text"] == ""
    await journey.click(
        (customer_cb if customer else cb)("qr", cid, old["q_token"]),
        actor=actor,
        message_id=old["q_panel"],
    )
    assert (
        isinstance(journey.transport.calls[-1], AnswerCallbackQuery)
        and journey.transport.calls[-1].show_alert
    )


async def test_native_emoji_and_html_are_preserved_in_immediate_save(journey, database, settings):
    from aiogram.types import MessageEntity
    from test_ads_content import rich_emoji_text

    cid = await create(database, settings)
    await click(journey, "qe", cid, "TEXT.2")
    text, entities = rich_emoji_text()
    entities.append({"type": "bold", "offset": 0, "length": 200})
    await journey.send(
        text, actor=900, entities=[MessageEntity.model_validate(e) for e in entities]
    )
    async with database() as session:
        assert (await AdsService(settings, session).current(900, cid))[1].content[
            "entities"
        ] == entities
    await click(journey, "qb", cid)
    await click(journey, "qe", cid, "TEXT.3")
    await journey.send("<b>New HTML</b>", actor=900)
    async with database() as session:
        content = (await AdsService(settings, session).current(900, cid))[1].content
        assert content["text"] == "New HTML" and content["entities"] == [
            {"type": "bold", "offset": 0, "length": 8}
        ]


async def test_controller_state_survives_redis_dispatcher_restart(database, settings):
    import os
    from uuid import uuid4

    from aiogram import Bot, Dispatcher
    from aiogram.fsm.storage.base import DefaultKeyBuilder
    from aiogram.fsm.storage.redis import RedisStorage
    from test_telegram import Journey, Transport

    from app.bot.handlers import create_router

    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for isolated real Redis restart checks")
    prefix = DefaultKeyBuilder(prefix="message_editor_" + uuid4().hex)
    first = RedisStorage.from_url(url, key_builder=prefix)
    second = RedisStorage.from_url(url, key_builder=prefix)
    transport = Transport()
    bot = Bot(settings.bot_token.get_secret_value(), session=transport)

    def make(storage):
        dispatcher = Dispatcher(storage=storage)
        dispatcher.include_router(create_router(settings, database))
        return Journey(bot, dispatcher, transport)

    try:
        cid = await create(database, settings)
        original = make(first)
        await click(original, "qe", cid, "TEXT.2")
        before = await data(original)
        resumed = make(second)
        assert (await data(resumed))["q_panel"] == before["q_panel"]
        await resumed.send("After restart", actor=900)
        assert (await data(resumed))["q_kind"] == "SET"
        async with database() as session:
            assert (await AdsService(settings, session).current(900, cid))[1].content[
                "text"
            ] == "After restart"
    finally:
        keys = [key async for key in first.redis.scan_iter(match=prefix.prefix + ":*")]
        if keys:
            await first.redis.delete(*keys)
        await first.close()
        await second.close()
        await bot.session.close()


async def test_postgres_received_save_serializes_duplicate_and_competing_admins(postgres_contract):
    import asyncio

    from app.errors import DomainError

    sessions, settings = postgres_contract
    cid = await create(sessions, settings)
    async with sessions() as session:
        ads = AdsService(settings, session)
        first = await ads.stage(
            900, cid, 2, "TEXT", {"text": "First", "entities": []}, "native-first"
        )
        first_id = first.id
        second = await ads.stage(
            901, cid, 2, "TEXT", {"text": "Second", "entities": []}, "native-second"
        )
        second_id = second.id

    async def apply(actor, did):
        async with sessions() as session:
            try:
                return (await AdsService(settings, session).save_received(actor, did)).id
            except DomainError as error:
                return error.code

    ids = await asyncio.gather(apply(900, first_id), apply(900, first_id))
    assert ids[0] == ids[1]
    assert await apply(901, second_id) == "ads_conflict"
    async with sessions() as session:
        assert (await session.get(AdCampaign, cid)).version == 3

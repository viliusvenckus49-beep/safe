"""Real aiogram routing and structured native Bot API previews; no group publication."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, SendMediaGroup, SendMessage, SendPhoto, SendVideo
from aiogram.types import MessageEntity
from sqlalchemy import select
from test_telegram import journey as journey

from app.ads_services import AdsService
from app.bot.ads import cb
from app.bot.callbacks import AdsPreview
from app.i18n import t
from app.models import AdCampaign, AdContentDraft
from app.services import Service


async def new(journey, database):
    await journey.send("/ads", actor=900)
    await journey.click(cb("new"), actor=900)
    await journey.send("INTERNAL_ONLY", actor=900)
    async with database() as session:
        return (await session.scalar(select(AdCampaign))).id


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_private_editor_preserves_entities_previews_then_saves(
    journey, database, settings, lang
):
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    cid = await new(journey, database)
    await journey.click(cb("edit", cid, "TEXT"), actor=900)
    text = "<b>Literal</b> 😀"
    entities = [
        MessageEntity(type="bold", offset=0, length=3),
        MessageEntity(
            type="custom_emoji",
            offset=len(text.encode("utf-16-le")) // 2 - 2,
            length=2,
            custom_emoji_id="12345",
        ),
    ]
    await journey.send(text, actor=900, entities=entities)
    async with database() as session:
        pending = await session.scalar(select(AdContentDraft))
        did = pending.id
        assert not pending.previewed
    await journey.click(cb("save", cid, did), actor=900)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 1
    await journey.click(cb("draft_preview", cid, did), actor=900)
    preview = [c for c in journey.transport.calls if isinstance(c, SendMessage) and c.text == text][
        -1
    ]
    assert preview.chat_id == 900 and preview.parse_mode is None
    assert preview.entities[1].custom_emoji_id == "12345"
    assert "INTERNAL_ONLY" not in preview.text
    await journey.click(cb("save", cid, did), actor=900)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 2
    assert t("ads.saved", lang, version=2) in journey.text()
    assert all(
        getattr(c, "chat_id", 900) == 900
        for c in journey.transport.calls
        if isinstance(c, (SendMessage, SendPhoto, SendVideo, SendMediaGroup))
    )


async def test_ordinary_user_and_group_context_cannot_reach_ads(journey, database):
    await journey.send("/ads", actor=1)
    await journey.click(cb("new"), actor=1)
    await journey.send("/ads", actor=900, chat=-100)
    async with database() as session:
        assert await session.scalar(select(AdCampaign)) is None
    assert not any(
        getattr(c, "reply_markup", None)
        and any(
            "ad3:edit" in (b.callback_data or "")
            for row in c.reply_markup.inline_keyboard
            for b in row
        )
        for c in journey.transport.calls
    )


async def test_album_collection_is_native_and_caption_does_not_change_text(
    journey, database, settings
):
    cid = await new(journey, database)
    async with database() as session:
        service = AdsService(settings, session)
        pending = await service.stage(
            900, cid, 1, "TEXT", {"text": "Caption stays", "entities": []}, "seed-text"
        )
        await service.mark_previewed(900, pending.id)
        await service.save(900, pending.id)
    await journey.click(cb("edit", cid, "MEDIA"), actor=900)
    await journey.send(
        actor=900,
        photo=[{"file_id": "photo_1", "file_unique_id": "p1", "width": 100, "height": 100}],
        caption="Must not replace text",
        media_group_id="album1",
    )
    await journey.send(
        actor=900,
        video={
            "file_id": "video_1",
            "file_unique_id": "v1",
            "width": 100,
            "height": 100,
            "duration": 1,
        },
        media_group_id="album1",
    )
    async with database() as session:
        did = (
            await session.scalar(select(AdContentDraft).where(AdContentDraft.field == "MEDIA"))
        ).id
    await journey.click(cb("finish", cid, did), actor=900)
    await journey.click(cb("draft_preview", cid, did), actor=900)
    album = [c for c in journey.transport.calls if isinstance(c, SendMediaGroup)][-1]
    assert album.chat_id == 900 and len(album.media) == 2
    assert album.media[0].caption == "Caption stays" and album.media[1].caption is None
    assert album.media[0].parse_mode is None
    await journey.click(cb("save", cid, did), actor=900)
    async with database() as session:
        content = (await AdsService(settings, session).current(900, cid))[1].content
        assert content["text"] == "Caption stays" and len(content["media"]) == 2


async def test_button_entities_are_native_and_popup_is_private_authorized(journey, database):
    cid = await new(journey, database)
    await journey.click(cb("edit", cid, "BUTTONS"), actor=900)
    await journey.send(
        "😀 Open - popup: Hello",
        actor=900,
        entities=[MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="123456")],
    )
    async with database() as session:
        did = (await session.scalar(select(AdContentDraft))).id
    await journey.click(cb("draft_preview", cid, did), actor=900)
    preview = [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage)
        and c.reply_markup
        and any(
            b.icon_custom_emoji_id == "123456"
            for row in c.reply_markup.inline_keyboard
            for b in row
        )
    ][-1]
    button = preview.reply_markup.inline_keyboard[0][0]
    assert button.icon_custom_emoji_id == "123456" and button.text == "Open"
    await journey.click(button.callback_data, actor=1)
    response = [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][-1]
    assert response.text != "Hello"
    await journey.click(button.callback_data, actor=900)
    assert [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)][
        -1
    ].text == "Hello"


async def test_telegram_rejection_does_not_mark_preview_success(journey, database, monkeypatch):
    cid = await new(journey, database)
    await journey.click(cb("edit", cid, "TEXT"), actor=900)
    await journey.send("Reject this", actor=900)
    async with database() as session:
        did = (await session.scalar(select(AdContentDraft))).id
    original = journey.transport.make_request

    async def reject(bot, method, timeout=None):
        if isinstance(method, SendMessage) and method.text == "Reject this":
            raise TelegramBadRequest(method=method, message="custom emoji unavailable")
        return await original(bot, method, timeout)

    monkeypatch.setattr(journey.transport, "make_request", reject)
    await journey.click(cb("draft_preview", cid, did), actor=900)
    async with database() as session:
        assert not (await session.get(AdContentDraft, did)).previewed
        assert (await session.get(AdCampaign, cid)).version == 1


async def test_album_buttons_are_a_separate_native_message(journey, database, settings):
    cid = await new(journey, database)
    async with database() as session:
        ads = AdsService(settings, session)
        first = await ads.stage(
            900,
            cid,
            1,
            "MEDIA",
            [{"kind": "photo", "file_id": "photo_one"}, {"kind": "photo", "file_id": "photo_two"}],
            "album-direct",
        )
        await ads.mark_previewed(900, first.id)
        await ads.save(900, first.id)
    await journey.click(cb("edit", cid, "BUTTONS"), actor=900)
    await journey.send("Open - https://t.me/Example", actor=900)
    async with database() as session:
        did = (
            await session.scalar(select(AdContentDraft).where(AdContentDraft.field == "BUTTONS"))
        ).id
    before = len(journey.transport.calls)
    await journey.click(cb("draft_preview", cid, did), actor=900)
    calls = journey.transport.calls[before:]
    album_index = next(i for i, c in enumerate(calls) if isinstance(c, SendMediaGroup))
    following = next(c for c in calls[album_index + 1 :] if isinstance(c, SendMessage))
    assert following.chat_id == 900
    assert following.reply_markup.inline_keyboard[0][0].url == "https://t.me/Example"
    assert t("ads.album_buttons") in following.text


async def test_clearing_media_is_independent_and_cancel_does_not_mutate_campaign(
    journey, database, settings
):
    cid = await new(journey, database)
    await journey.click(cb("edit", cid, "MEDIA"), actor=900)
    await journey.click(cb("clear", cid, "MEDIA.1"), actor=900)
    async with database() as session:
        pending = await session.scalar(
            select(AdContentDraft).where(AdContentDraft.status == "READY")
        )
        did = pending.id
        assert pending.content["media"] == []
    await journey.click(cb("cancel", cid, did), actor=900)
    async with database() as session:
        assert (await session.get(AdCampaign, cid)).version == 1
        assert (await session.get(AdContentDraft, did)).status == "CANCELLED"


@pytest.mark.parametrize("field", ["TEXT", "MEDIA", "BUTTONS"])
async def test_each_editor_opens_a_real_input_panel_without_callback_encoding_errors(
    journey, database, field
):
    cid = await new(journey, database)
    before = len(journey.transport.calls)
    await journey.click(cb("edit", cid, field), actor=900)
    from aiogram.methods import EditMessageText

    panels = [
        call for call in journey.transport.calls[before:] if isinstance(call, EditMessageText)
    ]
    assert len(panels) == 1
    assert panels[0].reply_markup is not None
    assert t("ads.error") not in panels[0].text
    if field == "MEDIA":
        assert t("ads.collecting", count=0) in panels[0].text
    else:
        assert panels[0].text == t("ads." + field.lower() + "_prompt")
    clear = [
        button
        for row in panels[0].reply_markup.inline_keyboard
        for button in row
        if button.text == t("ads.clear")
    ][0]
    assert ".1" in clear.callback_data


async def test_nontext_input_in_button_editor_does_not_stage_accidental_clear(journey, database):
    cid = await new(journey, database)
    await journey.click(cb("edit", cid, "BUTTONS"), actor=900)
    await journey.send(
        actor=900,
        photo=[{"file_id": "photo_1", "file_unique_id": "p1", "width": 100, "height": 100}],
        caption="not a button description",
    )
    async with database() as session:
        assert await session.scalar(select(AdContentDraft)) is None
        assert (await session.get(AdCampaign, cid)).version == 1
    await journey.send("Open - https://t.me/Example", actor=900)
    async with database() as session:
        pending = await session.scalar(select(AdContentDraft))
        assert pending.content["buttons"][0][0]["label"] == "Open"


@pytest.mark.parametrize("destination", ["history", "drafts", "delete", "restore", "draft"])
async def test_leaving_an_editor_never_keeps_hidden_text_input(
    journey, database, settings, destination
):
    cid = await new(journey, database)
    async with database() as session:
        ads = AdsService(settings, session)
        version = (await ads.current(900, cid))[1]
        pending = await ads.stage(900, cid, 1, "TEXT", {"text": "Draft", "entities": []}, "ready")
        value = (
            str(version.id)
            if destination == "restore"
            else pending.id
            if destination == "draft"
            else "1"
            if destination == "delete"
            else ""
        )
    await journey.click(cb("edit", cid, "TEXT"), actor=900)
    assert await journey.state(900) is not None
    await journey.click(cb(destination, cid, value), actor=900)
    assert await journey.state(900) is None
    await journey.send("This is not an ad edit", actor=900)
    async with database() as session:
        rows = list(await session.scalars(select(AdContentDraft)))
        assert not any(row.content["text"] == "This is not an ad edit" for row in rows)


async def test_forged_oversized_preview_version_is_rejected_cleanly(journey):
    await journey.click(AdsPreview(source="v" + str(2**80), row=0, column=0).pack(), actor=900)
    answers = [c for c in journey.transport.calls if isinstance(c, AnswerCallbackQuery)]
    assert len(answers) == 1 and answers[0].show_alert is True
    assert answers[0].text == t("ads.stale")

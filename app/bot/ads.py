"""Private native AUTO ADS editor. Stage 3.1 never publishes to a group."""

from hashlib import sha256
from html import escape
from typing import Any
from urllib.parse import urlencode

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
    MessageEntity,
)

from app.ads_content import (
    media_from_message,
    parse_buttons,
    text_from_message,
    validate_content,
)
from app.ads_services import AdsService
from app.bot.callbacks import AdPublication, AdsAction, AdsPreview, GlobalAd, PublishedAd
from app.bot.keyboards import action, keyboard
from app.bot.local_admin import display
from app.bot.states import AdsFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.i18n import t
from app.models import AdContentVersion
from app.moderation_services import utc
from app.services import Service


def cb(name: str, campaign: int = 0, value: str = "") -> str:
    return AdsAction(action=name, campaign=campaign, value=value).pack()


def callback_request(callback: CallbackQuery, operation: str) -> str:
    # Callback IDs are opaque API strings, not promised to be decimal/URL-safe.
    return f"ads:{operation}:{callback.from_user.id}:" + sha256(callback.id.encode()).hexdigest()


def navigation(campaign: int = 0) -> InlineKeyboardMarkup:
    return keyboard([[(t("ads.back"), cb("campaign", campaign) if campaign else cb("root"))]])


def failure(error: Exception) -> str:
    if isinstance(error, DomainError):
        key = {
            "forbidden": "permission",
            "group_moderation_invalid_id": "stale",
            "ads_text": "content",
            "ads_media": "media_error",
            "ads_buttons": "buttons_error",
        }.get(error.code, error.code.removeprefix("ads_"))
        return (
            t("ads." + key)
            if key
            in {
                "permission",
                "stale",
                "name",
                "content",
                "entity",
                "url",
                "rules",
                "media_error",
                "buttons_error",
                "conflict",
                "missing",
                "preview_required",
            }
            else t("ads.error")
        )
    return t("ads.telegram") if isinstance(error, TelegramAPIError) else t("ads.error")


async def private(service: Service, message: Message, actor: int) -> AdsService:
    if message.chat.type != "private" or message.chat.id != actor or message.bot is None:
        raise DomainError("forbidden")
    ads = AdsService(service.settings, service.session)
    await ads.authorize(actor)
    return ads


async def root(message: Message, ads: AdsService, actor: int, *, edit: bool = True) -> None:
    await ads.authorize(actor)
    drafts = await ads.unfinished(actor)
    rows = [
        [(t("ads.new"), cb("new")), ("🌐 Global ADS", GlobalAd(action="root").pack())],
        [(t("ads.list"), cb("list")), (t("ap.stats"), GlobalAd(action="stats").pack())],
        [(t("g.settings"), GlobalAd(action="settings").pack()), (t("ads.back"), action("admin"))],
    ]
    if drafts:
        rows.append([(t("ads.drafts"), cb("drafts"))])
    await ads.session.commit()
    await display(message, t("g.main") + "\n\n" + t("ads.draft_note"), keyboard(rows), edit=edit)


async def campaign_panel(
    message: Message, ads: AdsService, actor: int, campaign_id: int, *, edit: bool = True
) -> None:
    campaign, _ = await ads.current(actor, campaign_id)
    rows = [
        [
            (t("ads." + field.lower()), cb("edit", campaign.id, field)),
            (t("ads.preview"), cb("preview", campaign.id, field)),
        ]
        for field in ("TEXT", "MEDIA", "BUTTONS")
    ]
    rows += [
        [(t("ap.manage"), AdPublication(action="panel", campaign=campaign.id).pack())],
        [("🌐 Global ADS", GlobalAd(action="campaign", campaign=campaign.id).pack())],
        [(t("ads.full"), cb("preview", campaign.id, "FULL"))],
        [(t("ads.history"), cb("history", campaign.id))],
        [(t("ads.delete"), cb("delete", campaign.id, str(campaign.version)))],
        [(t("ads.back"), cb("list"))],
    ]
    text = (
        t("ads.title")
        + "\n\n"
        + t(
            "ads.campaign",
            name=escape(campaign.internal_name),
            id=campaign.id,
            version=campaign.version,
            note=t("ads.draft_note"),
        )
    )
    await ads.session.commit()
    await display(message, text, keyboard(rows), edit=edit)


async def draft_panel(
    message: Message, ads: AdsService, actor: int, draft_id: str, *, edit: bool = True
) -> None:
    draft = await ads.draft(actor, draft_id)
    rows = []
    if draft.status == "COLLECTING":
        text = t("ads.collecting", count=len(draft.content["media"]))
        rows.append([(t("ads.done"), cb("finish", draft.campaign_id, draft.id))])
        rows.append(
            [(t("ads.clear"), cb("clear", draft.campaign_id, f"MEDIA.{draft.base_version}"))]
        )
    else:
        text = t(
            "ads.ready",
            field=t("ads." + draft.field.lower()) if draft.field != "RESTORE" else t("ads.restore"),
        )
        rows.append([(t("ads.full"), cb("draft_preview", draft.campaign_id, draft.id))])
        if draft.previewed:
            rows.append([(t("ads.save"), cb("save", draft.campaign_id, draft.id))])
    rows.append([(t("ads.cancel"), cb("cancel", draft.campaign_id, draft.id))])
    await ads.session.commit()
    await display(message, t("ads.title") + "\n\n" + text, keyboard(rows), edit=edit)


def preview_keyboard(
    content: dict, source: str, *, public_attempt: int | None = None
) -> InlineKeyboardMarkup | None:
    rows = []
    for r, row in enumerate(content["buttons"]):
        buttons = []
        for c, item in enumerate(row):
            args: dict[str, Any] = {
                "text": item["label"],
                "icon_custom_emoji_id": item["icon_custom_emoji_id"],
            }
            kind, value = item["kind"], item["value"]
            if kind == "url":
                args["url"] = value
            elif kind == "share":
                # Native Telegram sharing URL; no inline-mode or Mini App dependency.
                args["url"] = "https://t.me/share/url?" + urlencode({"url": "", "text": value})
            elif kind == "copy":
                args["copy_text"] = CopyTextButton(text=value)
            else:
                args["callback_data"] = (
                    PublishedAd(attempt=public_attempt, row=r, column=c).pack()
                    if public_attempt is not None
                    else AdsPreview(source=source, row=r, column=c).pack()
                )
            buttons.append(InlineKeyboardButton(**args))
        rows.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


async def send_preview(
    message: Message,
    ads: AdsService,
    actor: int,
    campaign_id: int,
    content: dict,
    source: str,
    part: str = "FULL",
) -> None:
    if message.chat.type != "private" or message.chat.id != actor or message.bot is None:
        raise DomainError("forbidden")
    content = validate_content(content)
    bot = message.bot

    async def ready() -> None:
        await ads.get(actor, campaign_id)
        await ads.session.commit()  # No SQL locks across any Telegram request.

    if part == "BUTTONS":
        await ready()
        await bot.send_message(
            actor,
            t("ads.buttons_preview"),
            reply_markup=preview_keyboard(content, source),
            parse_mode="HTML",
        )
        return
    text = content["text"] if part != "MEDIA" else ""
    entities = (
        [MessageEntity.model_validate(e) for e in content["entities"]] if part != "MEDIA" else []
    )
    media = content["media"] if part != "TEXT" else []
    markup = preview_keyboard(content, source) if part == "FULL" else None
    await ready()
    if not media:
        await bot.send_message(
            actor,
            text or t("ads.empty_preview"),
            entities=entities,
            parse_mode=None,
            reply_markup=markup,
        )
    elif len(media) == 1:
        item = media[0]
        values: dict[str, Any] = {
            "chat_id": actor,
            "caption": text or None,
            "caption_entities": entities,
            "parse_mode": None,
            "reply_markup": markup,
        }
        if item["kind"] == "photo":
            await bot.send_photo(photo=item["file_id"], **values)
        else:
            await bot.send_video(video=item["file_id"], **values)
    else:
        album: list[Any] = []
        for index, item in enumerate(media):
            cls = InputMediaPhoto if item["kind"] == "photo" else InputMediaVideo
            album.append(
                cls(
                    media=item["file_id"],
                    caption=text or None if index == 0 else None,
                    caption_entities=entities if index == 0 else None,
                    parse_mode=None,
                )
            )
        await bot.send_media_group(actor, media=album)
        if markup:
            await ready()
            await bot.send_message(
                actor, t("ads.album_buttons"), reply_markup=markup, parse_mode="HTML"
            )


def register_ads_handlers(router: Router) -> None:
    @router.message(Command("ads"))
    async def open_ads(message: Message, state: FSMContext, service: Service) -> None:
        try:
            ads = await private(service, message, actor_id(message))
            await state.set_state(None)
            await root(message, ads, actor_id(message), edit=False)
        except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.message(AdsFlow.input, ~F.text.startswith("/"))
    async def input_ads(message: Message, state: FSMContext, service: Service) -> None:
        try:
            actor = actor_id(message)
            ads = await private(service, message, actor)
            data = await state.get_data()
            field = data["ads_field"]
            panel = message.model_copy(update={"message_id": data["ads_panel"]}).as_(message.bot)
            request = f"ads:{message.chat.id}:{message.message_id}"
            if field == "NAME":
                campaign = await ads.create(actor, message.text or "", request)
                await state.set_state(None)
                if data.get("global_new"):
                    from app.ads_global import GlobalAdsService
                    from app.ads_locks import ads_transaction
                    from app.bot.global_ads import campaign_panel as global_panel

                    global_ads = GlobalAdsService(service.settings, service.session)
                    await global_ads.policy(actor, campaign.id)
                    async with ads_transaction(service.session, campaign.id, policy=True):
                        policy = await global_ads.policy(actor, campaign.id)
                        policy.mode = "SELECTED"
                        policy.version += 1
                        global_ads.ads.audit(actor, "global_created", campaign.id)
                    await state.update_data(global_new=False)
                    await global_panel(panel, service, actor, campaign.id)
                else:
                    await campaign_panel(panel, ads, actor, campaign.id)
                return
            if field == "MEDIA":
                draft = await ads.append_media(
                    actor, data["ads_draft"], media_from_message(message), message.message_id
                )
                if not message.media_group_id:
                    await draft_panel(panel, ads, actor, draft.id)
                return
            value: Any
            if field == "TEXT":
                value = text_from_message(message)
            else:
                if message.text is None:
                    raise DomainError("ads_buttons")
                value = parse_buttons(
                    message.text,
                    [e.model_dump(mode="json", exclude_none=True) for e in message.entities or []],
                )
            draft = await ads.stage(
                actor, int(data["ads_campaign"]), int(data["ads_base"]), field, value, request
            )
            await state.set_state(None)
            await draft_panel(panel, ads, actor, draft.id)
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.callback_query(AdsPreview.filter())
    async def preview_button(
        callback: CallbackQuery, callback_data: AdsPreview, service: Service
    ) -> None:
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            actor, source = callback.from_user.id, callback_data.source
            ads = await private(service, callback.message, actor)
            if source.startswith("d"):
                draft = await ads.draft(actor, source[1:])
                content = draft.content
            elif source.startswith("v"):
                version_id = int(source[1:])
                ads.database_id(version_id)
                version = await service.session.get(AdContentVersion, version_id)
                if version is None:
                    raise DomainError("ads_missing")
                await ads.get(actor, version.campaign_id)
                content = version.content
            else:
                raise DomainError("ads_stale")
            if callback_data.row < 0 or callback_data.column < 0:
                raise DomainError("ads_stale")
            item = content["buttons"][callback_data.row][callback_data.column]
            if item["kind"] not in {"popup", "alert"}:
                raise DomainError("ads_stale")
            await service.session.commit()
            await callback.answer(item["value"], show_alert=item["kind"] == "alert")
        except (DomainError, ValueError, IndexError, KeyError, TelegramAPIError) as error:
            await service.session.rollback()
            await callback.answer(failure(error), show_alert=True)

    @router.callback_query(AdsAction.filter())
    async def ads_callback(
        callback: CallbackQuery, callback_data: AdsAction, state: FSMContext, service: Service
    ) -> None:
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            message, actor = callback.message, callback.from_user.id
            ads = await private(service, message, actor)
            name, cid, value = callback_data.action, callback_data.campaign, callback_data.value
            await callback.answer()
            acknowledged = True
            # Any management navigation closes the previous input context. Only
            # an explicit edit/recovered media collection may open it again.
            await state.set_state(None)
            if name == "root":
                await state.set_state(None)
                await root(message, ads, actor)
            elif name == "list":
                await state.set_state(None)
                page = max(0, min(10000, int(value or 0)))
                campaigns = await ads.campaigns(actor, page)
                rows = [[(c.internal_name[:50], cb("campaign", c.id))] for c in campaigns[:8]]
                if page:
                    rows.append([(t("ads.back"), cb("list", value=str(page - 1)))])
                if len(campaigns) > 8:
                    rows.append([(t("ads.next"), cb("list", value=str(page + 1)))])
                rows.append([(t("ads.back"), cb("root"))])
                await service.session.commit()
                await display(
                    message,
                    t("ads.title") + ("\n\n" + t("ads.empty_list") if not campaigns else ""),
                    keyboard(rows),
                    edit=True,
                )
            elif name == "campaign":
                await state.set_state(None)
                await campaign_panel(message, ads, actor, cid)
            elif name == "new":
                await state.set_state(AdsFlow.input)
                await state.update_data(
                    ads_field="NAME", ads_panel=message.message_id, global_new=False
                )
                await service.session.commit()
                await display(message, t("ads.name_prompt"), navigation(), edit=True)
            elif name == "edit":
                if value not in {"TEXT", "MEDIA", "BUTTONS"}:
                    raise DomainError("ads_stale")
                campaign, _ = await ads.current(actor, cid)
                base = campaign.version
                await state.set_state(AdsFlow.input)
                await state.update_data(
                    ads_field=value, ads_campaign=cid, ads_base=base, ads_panel=message.message_id
                )
                if value == "MEDIA":
                    draft = await ads.stage(
                        actor, cid, base, "MEDIA", None, callback_request(callback, "media")
                    )
                    await state.update_data(ads_draft=draft.id)
                    await draft_panel(message, ads, actor, draft.id)
                else:
                    rows = [
                        [(t("ads.clear"), cb("clear", cid, f"{value}.{base}"))],
                        [(t("ads.back"), cb("campaign", cid))],
                    ]
                    await service.session.commit()
                    await display(
                        message, t("ads." + value.lower() + "_prompt"), keyboard(rows), edit=True
                    )
            elif name == "clear":
                field, base_text = value.split(".", 1)
                if field not in {"TEXT", "MEDIA", "BUTTONS"}:
                    raise DomainError("ads_stale")
                payload = {"text": "", "entities": []} if field == "TEXT" else []
                draft = await ads.stage(
                    actor, cid, int(base_text), field, payload, callback_request(callback, "clear")
                )
                await state.set_state(None)
                await draft_panel(message, ads, actor, draft.id)
            elif name in {"draft", "finish", "draft_preview", "save", "cancel"}:
                draft = await ads.draft(actor, value, editable=name not in {"save", "cancel"})
                cid = draft.campaign_id
                if name == "finish":
                    draft = await ads.finish_media(actor, draft.id)
                    await state.set_state(None)
                elif name == "save":
                    version = await ads.save(actor, draft.id)
                    await state.set_state(None)
                    await display(
                        message,
                        t("ads.saved", version=version.revision),
                        navigation(cid),
                        edit=True,
                    )
                    return
                elif name == "cancel":
                    await ads.cancel(actor, draft.id)
                    await state.set_state(None)
                    await display(message, t("ads.cancelled"), navigation(cid), edit=True)
                    return
                elif name == "draft_preview":
                    if draft.status != "READY":
                        raise DomainError("ads_stale")
                    await send_preview(message, ads, actor, cid, draft.content, "d" + draft.id)
                    await ads.mark_previewed(actor, draft.id)
                elif draft.status == "COLLECTING":
                    await state.set_state(AdsFlow.input)
                    await state.update_data(
                        ads_field="MEDIA",
                        ads_campaign=cid,
                        ads_base=draft.base_version,
                        ads_draft=draft.id,
                        ads_panel=message.message_id,
                    )
                await draft_panel(message, ads, actor, draft.id)
            elif name == "preview":
                if value not in {"FULL", "TEXT", "MEDIA", "BUTTONS"}:
                    raise DomainError("ads_stale")
                _, version = await ads.current(actor, cid)
                await send_preview(
                    message, ads, actor, cid, version.content, "v" + str(version.id), value
                )
            elif name == "history":
                page = max(0, min(10000, int(value or 0)))
                versions = await ads.history(actor, cid, page)
                text = t("ads.history")
                rows = []
                for version in versions[:5]:
                    text += "\n\n" + t(
                        "ads.history_row",
                        revision=version.revision,
                        date=utc(version.created_at).strftime("%Y-%m-%d %H:%M UTC"),
                        actor=version.actor_id,
                    )
                    rows.append(
                        [
                            (
                                t("ads.restore") + f" {version.revision}",
                                cb("restore", cid, str(version.id)),
                            )
                        ]
                    )
                if page:
                    rows.append([(t("ads.back"), cb("history", cid, str(page - 1)))])
                if len(versions) > 5:
                    rows.append([(t("ads.next"), cb("history", cid, str(page + 1)))])
                rows.append([(t("ads.back"), cb("campaign", cid))])
                await service.session.commit()
                await display(message, text, keyboard(rows), edit=True)
            elif name == "restore":
                campaign, _ = await ads.current(actor, cid)
                draft = await ads.stage(
                    actor,
                    cid,
                    campaign.version,
                    "RESTORE",
                    int(value),
                    callback_request(callback, "restore"),
                )
                await draft_panel(message, ads, actor, draft.id)
            elif name == "drafts":
                drafts = await ads.unfinished(actor)
                rows = [
                    [
                        (
                            f"#{d.campaign_id} · {t('ads.media') if d.field == 'MEDIA' else t('ads.drafts')}",
                            cb("draft", d.campaign_id, d.id),
                        )
                    ]
                    for d in drafts
                ]
                rows.append([(t("ads.back"), cb("root"))])
                await service.session.commit()
                await display(message, t("ads.drafts"), keyboard(rows), edit=True)
            elif name == "delete":
                campaign = await ads.get(actor, cid)
                if campaign.version != int(value):
                    raise DomainError("ads_conflict")
                await service.session.commit()
                await display(
                    message,
                    t("ads.delete_confirm"),
                    keyboard(
                        [
                            [(t("ads.delete"), cb("archive", cid, value))],
                            [(t("ads.cancel"), cb("campaign", cid))],
                        ]
                    ),
                    edit=True,
                )
            elif name == "archive":
                await ads.archive(actor, cid, int(value))
                await state.set_state(None)
                await display(message, t("ads.deleted"), navigation(), edit=True)
            else:
                raise DomainError("ads_stale")
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            if not acknowledged:
                await callback.answer(failure(error), show_alert=True)
            elif isinstance(callback.message, Message):
                await display(callback.message, failure(error), navigation(), edit=True)
            structlog.get_logger().info(
                "ads_action_rejected",
                actor_id=callback.from_user.id,
                error_type=type(error).__name__,
            )

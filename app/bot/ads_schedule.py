"""Private, live-authorized one-group schedule UI and public delivered-ad callbacks."""

from datetime import date, datetime
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.ads_publication import PublicationService, publication_rights
from app.ads_schedule import INTERVALS, VILNIUS, local_datetime
from app.bot.ads import callback_request, private
from app.bot.ads import cb as content_cb
from app.bot.callbacks import AdPublication, PublishedAd
from app.bot.keyboards import keyboard
from app.bot.local_admin import display
from app.bot.states import AdScheduleFlow
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t
from app.models import AdContentVersion, AdPublicationAttempt, AdPublicationPart, ManagedGroup
from app.moderation_services import utc
from app.services import Service


def cb(action: str, cid: int, value: str = "") -> str:
    return AdPublication(action=action, campaign=cid, value=value).pack()


def local(value: datetime | None) -> str:
    return utc(value).astimezone(VILNIUS).strftime("%Y-%m-%d %H:%M %z") if value else t("ap.unset")


def back(cid: int):
    return keyboard([[(t("ads.back"), cb("panel", cid))]])


def failure(error: Exception) -> str:
    key = error.code if isinstance(error, DomainError) else "ap.error"
    if key not in {
        "ap_time",
        "ap_dst",
        "ap_dates",
        "ap_interval",
        "ap_rights",
        "ap_group",
        "ap_incomplete",
        "ap_preview",
        "ap_content",
        "ap_running",
        "ap_multiple",
    }:
        key = "ap_stale" if isinstance(error, DomainError) else "ap_error"
    aliases = {"ap_group": "ap.group_error", "ap_content": "ap.content_error"}
    return t(aliases.get(key, key.replace("ap_", "ap.", 1)))


async def panel(message: Message, pub: PublicationService, actor: int, cid: int) -> None:
    row = await pub.ensure(actor, cid)
    campaign = await pub.ads.get(actor, cid)
    group = await pub.session.get(ManagedGroup, row.chat_id) if row.chat_id else None
    text = (
        t("ap.title")
        + "\n\n📋 <b>"
        + escape(campaign.internal_name)
        + "</b>\n\n"
        + t(
            "ap.summary",
            status=t("ap." + row.status),
            group=escape(group.title) if group else t("ap.unset"),
            interval=t("ap.i" + str(row.interval_seconds))
            if row.interval_seconds
            else t("ap.unset"),
            start=local(row.start_at),
            end=local(row.end_at) if row.end_at else t("ap.unlimited"),
            next=local(row.next_run_at),
        )
    )
    if row.last_error:
        text += "\n" + t("ap.last_error") + ": " + escape(row.last_error)
    rows = [
        [(t("ap.intervals"), cb("intervals", cid)), (t("ap.calendar"), cb("calendar", cid))],
        [(t("ap.group"), cb("groups", cid)), (t("ap.stats"), cb("stats", cid))],
        [(t("ap.start"), cb("start", cid)), (t("ap.pause"), cb("pause", cid, str(row.version)))],
        [(t("ap.stop"), cb("stop", cid, str(row.version)))],
        [
            (t("ads.preview"), content_cb("preview", cid, "FULL")),
            (t("ads.history"), cb("history", cid)),
        ],
        [(t("ap.content"), content_cb("campaign", cid))],
        [(t("ads.back"), content_cb("list"))],
    ]
    await pub.session.commit()
    await display(message, text, keyboard(rows), edit=True)


def register_publication_handlers(router: Router) -> None:
    @router.callback_query(PublishedAd.filter())
    async def public_button(
        callback: CallbackQuery, callback_data: PublishedAd, service: Service
    ) -> None:
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("ap_stale")
            d = callback_data
            PublicationService(service.settings, service.session).ads.database_id(d.attempt)
            row = await service.session.get(AdPublicationAttempt, d.attempt)
            if row is None or row.chat_id != callback.message.chat.id or d.row < 0 or d.column < 0:
                raise DomainError("ap_stale")
            parts = await service.session.scalars(
                select(AdPublicationPart).where(
                    AdPublicationPart.attempt_id == row.id, AdPublicationPart.status == "SENT"
                )
            )
            if not any(
                part.kind != "ALBUM" and callback.message.message_id in part.message_ids
                for part in parts
            ):
                raise DomainError("ap_stale")
            version = await service.session.get(AdContentVersion, row.content_version_id)
            if version is None:
                raise DomainError("ap_stale")
            item = version.content["buttons"][d.row][d.column]
            if item["kind"] not in {"popup", "alert"}:
                raise DomainError("ap_stale")
            await service.session.commit()
            await callback.answer(item["value"], show_alert=item["kind"] == "alert")
        except (DomainError, ValueError, IndexError, KeyError):
            await service.session.rollback()
            await callback.answer(t("ap.stale"), show_alert=True)

    @router.message(AdScheduleFlow.input, ~F.text.startswith("/"))
    async def schedule_input(message: Message, state: FSMContext, service: Service) -> None:
        try:
            actor = message.from_user.id if message.from_user else 0
            await private(service, message, actor)
            data = await state.get_data()
            value: Any = message.text or ""
            if data["ap_field"] == "start_date":
                value = date.fromisoformat(value.strip())
            elif data["ap_field"] == "end_at":
                value = None if value.strip() == "-" else local_datetime(value)
            pub = PublicationService(service.settings, service.session)
            await pub.change(
                actor,
                data["ap_cid"],
                data["ap_version"],
                data["ap_field"],
                value,
                f"schedule:{actor}:{message.message_id}",
                message.bot,
            )
            await state.set_state(None)
            target = message.model_copy(update={"message_id": data["ap_panel"]}).as_(message.bot)
            await panel(target, pub, actor, data["ap_cid"])
        except (
            DomainError,
            ValueError,
            KeyError,
            TelegramAPIError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.callback_query(AdPublication.filter())
    async def management(
        callback: CallbackQuery, callback_data: AdPublication, state: FSMContext, service: Service
    ) -> None:
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("ap_stale")
            actor, message = callback.from_user.id, callback.message
            await private(service, message, actor)
            cid, action, value = callback_data.campaign, callback_data.action, callback_data.value
            pub = PublicationService(service.settings, service.session)
            await pub.ads.get(actor, cid)
            await callback.answer()
            acknowledged = True
            await state.set_state(None)
            row = await pub.ensure(actor, cid)
            if action == "panel":
                await panel(message, pub, actor, cid)
            elif action == "intervals":
                choices = [
                    (t("ap.i" + str(i)), cb("interval", cid, f"{row.version}.{i}"))
                    for i in INTERVALS
                ]
                rows = [choices[i : i + 2] for i in range(0, len(choices), 2)]
                rows.append([(t("ads.back"), cb("panel", cid))])
                await display(message, t("ap.intervals"), keyboard(rows), edit=True)
            elif action == "interval":
                version, seconds = map(int, value.split("."))
                await pub.change(
                    actor,
                    cid,
                    version,
                    "interval_seconds",
                    seconds,
                    callback_request(callback, "interval"),
                    message.bot,
                )
                await panel(message, pub, actor, cid)
            elif action == "calendar":
                rows = [
                    [(t("ap." + f), cb("edit", cid, f"{row.version}.{f}"))]
                    for f in ("start_date", "start_time", "end_at")
                ]
                rows.append([(t("ads.back"), cb("panel", cid))])
                await display(
                    message, t("ap.calendar") + "\n\n" + t("ap.zone"), keyboard(rows), edit=True
                )
            elif action == "edit":
                version_text, field = value.split(".", 1)
                if int(version_text) != row.version or field not in {
                    "start_date",
                    "start_time",
                    "end_at",
                }:
                    raise DomainError("ap_stale")
                await state.set_state(AdScheduleFlow.input)
                await state.update_data(
                    ap_cid=cid, ap_version=row.version, ap_field=field, ap_panel=message.message_id
                )
                await display(message, t("ap.prompt_" + field), back(cid), edit=True)
            elif action == "groups":
                page = max(0, min(int(value or 0), 10000))
                groups = list(
                    await service.session.scalars(
                        select(ManagedGroup)
                        .where(
                            ManagedGroup.approved.is_(True),
                            ManagedGroup.enabled.is_(True),
                            ManagedGroup.chat_type.in_(("group", "supergroup")),
                            ~GroupService.removed_group(ManagedGroup.chat_id),
                        )
                        .order_by(ManagedGroup.chat_id)
                        .offset(page * 8)
                        .limit(9)
                    )
                )
                await service.session.commit()
                rows = []
                for group in groups[:8]:
                    try:
                        await publication_rights(message.bot, group.chat_id, actor)
                    except (DomainError, TelegramAPIError, OSError, TimeoutError):
                        continue
                    rows.append(
                        [(group.title[:50], cb("group", cid, f"{row.version}.{group.chat_id}"))]
                    )
                if page:
                    rows.append([(t("ads.back"), cb("groups", cid, str(page - 1)))])
                if len(groups) > 8:
                    rows.append([(t("ads.next"), cb("groups", cid, str(page + 1)))])
                rows.append([(t("ads.back"), cb("panel", cid))])
                await display(
                    message, t("ap.group") + "\n\n" + t("ap.group_note"), keyboard(rows), edit=True
                )
            elif action == "group":
                version, chat = map(int, value.split("."))
                await pub.change(
                    actor,
                    cid,
                    version,
                    "chat_id",
                    chat,
                    callback_request(callback, "group"),
                    message.bot,
                )
                await panel(message, pub, actor, cid)
            elif action == "start":
                row, content_version = await pub.validate(actor, cid)
                assert row.chat_id is not None
                group = await pub.group(row.chat_id)
                campaign = await pub.ads.get(actor, cid)
                await service.session.commit()
                await publication_rights(message.bot, row.chat_id, actor)
                text = t(
                    "ap.confirm",
                    name=escape(campaign.internal_name),
                    group=escape(group.title),
                    interval=t("ap.i" + str(row.interval_seconds)),
                    start=local(row.start_at),
                    end=local(row.end_at) if row.end_at else t("ap.unlimited"),
                )
                await display(
                    message,
                    text,
                    keyboard(
                        [
                            [
                                (
                                    t("ap.confirm_button"),
                                    cb("confirm", cid, f"{row.version}.{content_version.revision}"),
                                )
                            ],
                            [(t("ads.cancel"), cb("panel", cid))],
                        ]
                    ),
                    edit=True,
                )
            elif action == "confirm":
                expected, revision = map(int, value.split("."))
                await pub.activate(actor, cid, expected, revision, message.bot)
                await panel(message, pub, actor, cid)
            elif action in {"pause", "stop"}:
                await pub.control(
                    actor,
                    cid,
                    int(value),
                    "PAUSED" if action == "pause" else "STOPPED",
                    message.bot,
                )
                await display(message, t("ap.control_note"), back(cid), edit=True)
            elif action == "stats":
                stats = await pub.statistics(actor, cid)
                await service.session.commit()
                await display(
                    message,
                    t(
                        "ap.stats_text",
                        sent=stats["sent"],
                        failed=stats["failed"],
                        uncertain=stats.get("uncertain", 0),
                        last=local(stats["last"]),
                        next=local(row.next_run_at),
                    ),
                    back(cid),
                    edit=True,
                )
            elif action == "history":
                attempts = await pub.history(actor, cid, limit=5)
                text = t("ads.history")
                for attempt in attempts:
                    details = await pub.parts(attempt.id)
                    pinned = await service.session.get(AdContentVersion, attempt.content_version_id)
                    text += (
                        "\n\n"
                        + f"#{attempt.id} · <b>{t('ap.' + attempt.status)}</b> · {local(attempt.due_at)}"
                    )
                    text += f"\nID: <code>{attempt.chat_id}</code> · v{pinned.revision if pinned else '?'}"
                    for part in details:
                        text += "\n" + escape(
                            f"{t('ap.kind_' + part.kind)}: {t('ap.' + part.status)} · {part.message_ids}"
                        )
                    if attempt.error:
                        text += "\n" + escape(attempt.error)
                await service.session.commit()
                await display(message, text, back(cid), edit=True)
            else:
                raise DomainError("ap_stale")
        except (
            DomainError,
            ValueError,
            KeyError,
            TelegramAPIError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            if acknowledged and isinstance(callback.message, Message):
                await display(
                    callback.message, failure(error), back(callback_data.campaign), edit=True
                )
            else:
                await callback.answer(failure(error), show_alert=True)

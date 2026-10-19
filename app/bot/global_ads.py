"""Native GLOBAL ADS navigation; every action is private, freshly authorized and versioned."""

from datetime import date, datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.ads_global import GlobalAdsService
from app.ads_publication import PublicationService, publication_rights
from app.ads_schedule import INTERVALS, local_datetime
from app.bot.ads import callback_request, private
from app.bot.ads import cb as content_cb
from app.bot.ads_schedule import cb as schedule_cb
from app.bot.ads_schedule import failure, local
from app.bot.callbacks import GlobalAd
from app.bot.keyboards import keyboard
from app.bot.local_admin import display
from app.bot.states import AdsFlow, GlobalAdsFlow
from app.errors import DomainError
from app.i18n import t
from app.models import (
    AdCampaign,
    AdGlobalOperationItem,
    AdPublicationAttempt,
    AdSchedule,
    ManagedGroup,
)
from app.services import Service


def cb(action, cid=0, value=""):
    return GlobalAd(action=action, campaign=cid, value=value).pack()


def base36(value):
    if value < 0:
        return "-" + base36(-value)
    alphabet, result = "0123456789abcdefghijklmnopqrstuvwxyz", ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result or "0"


async def show(message, service, text, rows):
    await service.session.commit()
    await display(message, text, keyboard(rows), edit=True)


async def campaign_panel(message, service, actor, cid):
    campaign = await GlobalAdsService(service.settings, service.session).ads.get(actor, cid)
    rows = [
        [
            (t("ads.text"), content_cb("edit", cid, "TEXT")),
            (t("ads.media"), content_cb("edit", cid, "MEDIA")),
        ],
        [
            (t("ads.buttons"), content_cb("edit", cid, "BUTTONS")),
            (t("ads.preview"), content_cb("preview", cid, "FULL")),
        ],
        [
            (t("g.groups"), cb("groups", cid)),
            (t("ap.intervals"), cb("schedules", cid, "0.intervals")),
        ],
        [(t("ap.calendar"), cb("schedules", cid, "0.calendar")), (t("ap.stats"), cb("stats", cid))],
        [(t("g.start"), cb("bulk", cid, "START")), (t("g.pause"), cb("bulk", cid, "PAUSE"))],
        [(t("g.stop"), cb("bulk", cid, "STOP")), (t("g.defaults"), cb("defaults", cid))],
        [(t("ads.history"), cb("history", cid)), (t("g.content"), content_cb("campaign", cid))],
        [(t("ads.delete"), content_cb("delete", cid, str(campaign.version)))],
        [(t("ads.back"), cb("list"))],
    ]
    await show(
        message,
        service,
        "📢 <b>" + escape(campaign.internal_name) + "</b>\n\n" + t("g.content_note"),
        rows,
    )


async def schedule_list(
    message, service, actor, cid, page=0, destination="panel", *, excluded=False
):
    global_ads = GlobalAdsService(service.settings, service.session)
    schedules = await global_ads.schedules(actor, cid, page=page, include_excluded=excluded)
    groups = {
        g.chat_id: g.title
        for g in await service.session.scalars(
            select(ManagedGroup).where(ManagedGroup.chat_id.in_([r.chat_id for r in schedules]))
        )
    }
    rows = []
    for row in schedules[:8]:
        label = groups.get(row.chat_id, str(row.chat_id))
        status = t("g.excluded") if row.excluded else t("ap." + row.status)
        interval = t("ap.i" + str(row.interval_seconds)) if row.interval_seconds else t("ap.unset")
        rows.append(
            [
                (
                    f"{label[:30]} · {interval} · {status}",
                    schedule_cb("history" if row.excluded else destination, cid, sid=row.id),
                )
            ]
        )
    if page:
        rows.append(
            [
                (
                    t("ads.back"),
                    cb("removed" if excluded else "schedules", cid, f"{page - 1}.{destination}"),
                )
            ]
        )
    if len(schedules) > 8:
        rows.append(
            [
                (
                    t("ads.next"),
                    cb("removed" if excluded else "schedules", cid, f"{page + 1}.{destination}"),
                )
            ]
        )
    if not excluded:
        rows += [[(t("g.removed"), cb("removed", cid))], [(t("g.assign"), cb("groups", cid))]]
    rows.append([(t("ads.back"), cb("campaign", cid))])
    await show(
        message, service, t("g.schedules") + ("\n\n" + t("g.empty") if not schedules else ""), rows
    )


async def operation_panel(message, service, actor, oid, page=0):
    global_ads = GlobalAdsService(service.settings, service.session)
    op, counts = await global_ads.progress(actor, oid)
    items = list(
        await service.session.scalars(
            select(AdGlobalOperationItem)
            .where(AdGlobalOperationItem.operation_id == oid)
            .order_by(AdGlobalOperationItem.chat_id)
            .offset(page * 8)
            .limit(9)
        )
    )
    text = t(
        "g.operation",
        action=t("g.action_" + op.action),
        pending=counts.get("PENDING", 0) + counts.get("CLAIMED", 0),
        success=counts.get("SUCCEEDED", 0),
        failed=counts.get("FAILED", 0),
        total=sum(counts.values()),
    )
    if op.action == "ASSIGN":
        text += "\n\n" + t("g.assignment_note", mode=t("g.mode_" + op.payload["mode"]))
    if op.action in {"DEFAULTS", "INHERIT", "CHANGE"}:
        text += "\n\n" + t("g.defaults_confirm")
    if op.action == "FUTURE_ON":
        policy = await global_ads.policy(actor, op.campaign_id)
        text += "\n\n" + t(
            "g.default_values",
            interval=t("ap.i" + str(policy.interval_seconds)),
            start=local(policy.start_at),
            end=local(policy.end_at) if policy.end_at else t("ap.unlimited"),
            mode=t("g.mode_" + policy.mode),
            auto=t("g.yes"),
        )
        text += "\n\n" + t("g.future_warning")
    if op.action == "CHANGE":
        field, value = op.payload["field"], op.payload["value"]
        label = {
            "interval_seconds": t("ap.intervals"),
            "start_date": t("ap.start_date"),
            "start_time": t("ap.start_time"),
            "end_at": t("ap.end_at"),
        }.get(field, t("ap.calendar"))
        rendered = (
            t("ap.i" + str(value))
            if field == "interval_seconds"
            else (t("ap.unlimited") if value is None else str(value))
        )
        text += "\n\n" + label + ": " + escape(rendered)
    for item in items[:8]:
        if item.error:
            text += "\n" + str(item.chat_id) + ": " + escape(item.error[:160])
    rows = []
    if op.status == "READY":
        rows += [
            [
                (
                    t("ap.confirm_button"),
                    cb("confirm", op.campaign_id, oid + "." + base36(op.version)),
                )
            ],
            [(t("ads.cancel"), cb("cancel", op.campaign_id, oid))],
        ]
    elif op.status == "RUNNING":
        rows.append([(t("g.refresh"), cb("progress", op.campaign_id, oid + "." + str(page)))])
    if page:
        rows.append([(t("ads.back"), cb("progress", op.campaign_id, oid + "." + str(page - 1)))])
    if len(items) > 8:
        rows.append([(t("ads.next"), cb("progress", op.campaign_id, oid + "." + str(page + 1)))])
    rows.append([(t("ads.back"), cb("campaign", op.campaign_id))])
    await show(message, service, text + "\n\n" + t("ap.control_note"), rows)


async def confirm_change(message, service, actor, cid, sid, expected, field, value, request):
    serialized = value.isoformat() if isinstance(value, (date, datetime)) else value
    op = await GlobalAdsService(service.settings, service.session).prepare(
        actor,
        cid,
        "CHANGE",
        request,
        message.bot,
        sid=sid,
        expected_version=expected,
        payload={"field": field, "value": serialized},
    )
    await operation_panel(message, service, actor, op.id)


async def selection_panel(message, service, actor, oid, page=0):
    global_ads = GlobalAdsService(service.settings, service.session)
    op = await global_ads.operation(actor, oid)
    if op.status != "SELECTING":
        await operation_panel(message, service, actor, oid)
        return
    groups = await global_ads.groups(actor, page=page)
    selected = set(op.payload["selected"])
    await service.session.commit()
    rows = []
    for group in groups[:8]:
        try:
            await publication_rights(message.bot, group.chat_id, actor)
        except (DomainError, TelegramAPIError, TimeoutError, OSError):
            continue
        value = ".".join((oid, base36(op.version), base36(group.chat_id), base36(page)))
        rows.append(
            [
                (
                    ("☑ " if group.chat_id in selected else "☐ ") + group.title[:42],
                    cb("toggle", op.campaign_id, value),
                )
            ]
        )
    if page:
        rows.append([(t("ads.back"), cb("select", op.campaign_id, oid + "." + str(page - 1)))])
    if len(groups) > 8:
        rows.append([(t("ads.next"), cb("select", op.campaign_id, oid + "." + str(page + 1)))])
    rows += [
        [(t("ap.confirm_button"), cb("ready", op.campaign_id, oid + "." + base36(op.version)))],
        [(t("ads.back"), cb("groups", op.campaign_id))],
    ]
    await show(message, service, t("g.choose", count=len(selected)), rows)


def register_global_ads_handlers(router: Router):
    @router.message(GlobalAdsFlow.input, ~F.text.startswith("/"))
    async def input_default(message: Message, state: FSMContext, service: Service):
        try:
            actor = message.from_user.id if message.from_user else 0
            await private(service, message, actor)
            data = await state.get_data()
            value = (
                None if (message.text or "").strip() == "-" else local_datetime(message.text or "")
            )
            await GlobalAdsService(service.settings, service.session).defaults(
                actor, data["g_cid"], data["g_version"], data["g_field"], value
            )
            await state.set_state(None)
            target = message.model_copy(update={"message_id": data["g_panel"]}).as_(message.bot)
            await defaults_panel(target, service, actor, data["g_cid"])
        except (
            DomainError,
            ValueError,
            IndexError,
            TypeError,
            KeyError,
            TelegramAPIError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.callback_query(GlobalAd.filter())
    async def management(
        callback: CallbackQuery, callback_data: GlobalAd, state: FSMContext, service: Service
    ):
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("ap_stale")
            message, actor = callback.message, callback.from_user.id
            await private(service, message, actor)
            d = callback_data
            global_ads = GlobalAdsService(service.settings, service.session)
            if d.campaign:
                await global_ads.ads.get(
                    actor, d.campaign, archived=d.action in {"history", "progress"}
                )
            await service.session.commit()
            await callback.answer()
            acknowledged = True
            await state.set_state(None)
            if d.action == "root":
                rows = [
                    [(t("g.new"), cb("new"))],
                    [(t("g.list"), cb("list"))],
                    [(t("g.manage_groups"), cb("managed"))],
                    [(t("g.stats"), cb("stats"))],
                    [(t("g.operations"), cb("operations"))],
                    [(t("ads.back"), content_cb("root"))],
                ]
                await show(message, service, t("g.title"), rows)
            elif d.action == "new":
                await state.set_state(AdsFlow.input)
                await state.update_data(
                    ads_field="NAME", ads_panel=message.message_id, global_new=True
                )
                await show(message, service, t("ads.name_prompt"), [[(t("ads.back"), cb("root"))]])
            elif d.action == "operations":
                from app.models import AdGlobalOperation

                page = max(0, min(10000, int(d.value or 0)))
                operations = list(
                    await service.session.scalars(
                        select(AdGlobalOperation)
                        .where(
                            AdGlobalOperation.actor_id == actor,
                            AdGlobalOperation.status.in_(("SELECTING", "READY", "RUNNING")),
                        )
                        .order_by(AdGlobalOperation.created_at.desc())
                        .offset(page * 8)
                        .limit(9)
                    )
                )
                rows = [
                    [
                        (
                            t("g.action_" + op.action) + " · " + t("g.op_" + op.status),
                            cb("resume", op.campaign_id, op.id),
                        )
                    ]
                    for op in operations[:8]
                ]
                if page:
                    rows.append([(t("ads.back"), cb("operations", value=str(page - 1)))])
                if len(operations) > 8:
                    rows.append([(t("ads.next"), cb("operations", value=str(page + 1)))])
                rows.append([(t("ads.back"), cb("root"))])
                await show(message, service, t("g.operations"), rows)
            elif d.action == "resume":
                op = await global_ads.operation(actor, d.value)
                if op.campaign_id != d.campaign:
                    raise DomainError("ap_stale")
                if op.status == "SELECTING" and op.payload.get("mode") in {"SINGLE", "SELECTED"}:
                    await selection_panel(message, service, actor, op.id)
                else:
                    if op.status == "SELECTING":
                        op = await global_ads.ready_selection(actor, op.id, op.version, message.bot)
                    await operation_panel(message, service, actor, op.id)
            elif d.action in {"list", "settings"}:
                page = max(0, min(10000, int(d.value or 0)))
                campaigns = list(
                    await service.session.scalars(
                        select(AdCampaign)
                        .where(AdCampaign.status == "DRAFT")
                        .order_by(AdCampaign.id.desc())
                        .offset(page * 8)
                        .limit(9)
                    )
                )
                rows = [
                    [
                        (
                            c.internal_name[:48],
                            cb("defaults" if d.action == "settings" else "campaign", c.id),
                        )
                    ]
                    for c in campaigns[:8]
                ]
                if page:
                    rows.append([(t("ads.back"), cb(d.action, value=str(page - 1)))])
                if len(campaigns) > 8:
                    rows.append([(t("ads.next"), cb(d.action, value=str(page + 1)))])
                rows.append([(t("ads.back"), cb("root"))])
                await show(
                    message,
                    service,
                    t("g.settings" if d.action == "settings" else "g.list")
                    + "\n\n"
                    + t("g.content_note"),
                    rows,
                )
            elif d.action == "campaign":
                await campaign_panel(message, service, actor, d.campaign)
            elif d.action == "groups":
                policy = await global_ads.policy(actor, d.campaign)
                rows = [
                    [
                        (
                            ("◉ " if policy.mode == mode else "○ ") + t("g.mode_" + mode),
                            cb("mode", d.campaign, mode),
                        )
                    ]
                    for mode in ["SINGLE", "SELECTED", "EXISTING", "FUTURE"]
                ]
                rows += [
                    [(t("g.schedules"), cb("schedules", d.campaign))],
                    [(t("ads.back"), cb("campaign", d.campaign))],
                ]
                await show(message, service, t("g.choose_mode"), rows)
            elif d.action == "mode":
                op = await global_ads.selection(
                    actor, d.campaign, d.value, callback_request(callback, "global-select")
                )
                if d.value in {"EXISTING", "FUTURE"}:
                    op = await global_ads.ready_selection(actor, op.id, op.version, message.bot)
                    await operation_panel(message, service, actor, op.id)
                else:
                    await selection_panel(message, service, actor, op.id)
            elif d.action in {"select", "toggle", "ready"}:
                values = d.value.split(".")
                op = await global_ads.operation(actor, values[0])
                if op.campaign_id != d.campaign:
                    raise DomainError("ap_stale")
                if d.action == "toggle":
                    op = await global_ads.toggle(
                        actor, op.id, int(values[1], 36), int(values[2], 36), message.bot
                    )
                    await selection_panel(message, service, actor, op.id, int(values[3], 36))
                elif d.action == "ready":
                    op = await global_ads.ready_selection(
                        actor, op.id, int(values[1], 36), message.bot
                    )
                    await operation_panel(message, service, actor, op.id)
                else:
                    await selection_panel(
                        message, service, actor, op.id, max(0, min(10000, int(values[1])))
                    )
            elif d.action in {"schedules", "removed"}:
                values = (d.value or "0.panel").split(".")
                destination = values[1] if len(values) > 1 else "panel"
                if destination not in {"panel", "calendar", "intervals", "history"}:
                    raise DomainError("ap_stale")
                await schedule_list(
                    message,
                    service,
                    actor,
                    d.campaign,
                    max(0, min(10000, int(values[0]))),
                    destination,
                    excluded=d.action == "removed",
                )
            elif d.action in {"bulk", "remove", "inherit", "apply", "future"}:
                sid, expected = None, None
                if d.action in {"remove", "inherit"}:
                    sid, expected = map(int, d.value.split("."))
                action = {"remove": "REMOVE", "inherit": "INHERIT", "apply": "DEFAULTS"}.get(
                    d.action, d.value
                )
                op = await global_ads.prepare(
                    actor,
                    d.campaign,
                    action,
                    callback_request(callback, "global-" + action.lower()),
                    message.bot,
                    sid=sid,
                    expected_version=expected,
                )
                await operation_panel(message, service, actor, op.id)
            elif d.action in {"confirm", "progress", "cancel"}:
                values = d.value.split(".")
                op = await global_ads.operation(actor, values[0])
                if op.campaign_id != d.campaign:
                    raise DomainError("ap_stale")
                if d.action == "confirm":
                    op = await global_ads.confirm(actor, op.id, int(values[1], 36))
                    await global_ads.run(op.id, message.bot, limit=8)
                elif d.action == "cancel":
                    await global_ads.cancel(actor, op.id)
                    await campaign_panel(message, service, actor, d.campaign)
                    return
                else:
                    await global_ads.run(op.id, message.bot, limit=8)
                await operation_panel(
                    message,
                    service,
                    actor,
                    op.id,
                    max(0, min(10000, int(values[1])))
                    if d.action == "progress" and len(values) > 1
                    else 0,
                )
            elif d.action == "defaults":
                await defaults_panel(message, service, actor, d.campaign)
            elif d.action == "di":
                policy = await global_ads.policy(actor, d.campaign)
                choices = [
                    (t("ap.i" + str(i)), cb("dv", d.campaign, f"{policy.version}.{i}"))
                    for i in INTERVALS
                ]
                rows = [choices[i : i + 2] for i in range(0, len(choices), 2)] + [
                    [(t("ads.back"), cb("defaults", d.campaign))]
                ]
                await show(message, service, t("g.defaults_note"), rows)
            elif d.action == "dv":
                version, interval = map(int, d.value.split("."))
                await global_ads.defaults(actor, d.campaign, version, "interval_seconds", interval)
                await defaults_panel(message, service, actor, d.campaign)
            elif d.action == "de":
                version_text, field = d.value.split(".")
                if field not in {"start_at", "end_at"}:
                    raise DomainError("ap_stale")
                policy = await global_ads.policy(actor, d.campaign)
                if policy.version != int(version_text):
                    raise DomainError("ap_stale")
                await state.set_state(GlobalAdsFlow.input)
                await state.update_data(
                    g_cid=d.campaign,
                    g_version=policy.version,
                    g_field=field,
                    g_panel=message.message_id,
                )
                await show(
                    message,
                    service,
                    t("g.date_prompt"),
                    [[(t("ads.back"), cb("defaults", d.campaign))]],
                )
            elif d.action == "stats":
                stats = await global_ads.statistics(actor, d.campaign or None)
                text = t(
                    "g.statistics",
                    **{**stats, "last": local(stats["last"]), "next": local(stats["next"])},
                )
                rows = (
                    [[(t("g.schedules"), cb("schedules", d.campaign))]]
                    if d.campaign
                    else [[(t("g.list"), cb("list"))]]
                )
                rows.append(
                    [(t("ads.back"), cb("campaign", d.campaign) if d.campaign else cb("root"))]
                )
                await show(message, service, text, rows)
            elif d.action == "history":
                page = max(0, min(10000, int(d.value or 0)))
                attempts = list(
                    await service.session.scalars(
                        select(AdPublicationAttempt)
                        .join(AdSchedule)
                        .where(AdSchedule.campaign_id == d.campaign)
                        .order_by(AdPublicationAttempt.id.desc())
                        .offset(page * 3)
                        .limit(4)
                    )
                )
                text = t("ads.history")
                for attempt in attempts[:3]:
                    text += f"\n\n#{attempt.id} · <code>{attempt.chat_id}</code> · {t('ap.' + attempt.status)}\n{local(attempt.due_at)}"
                    if attempt.error:
                        text += "\n" + escape(attempt.error)
                rows = []
                if page:
                    rows.append([(t("ads.back"), cb("history", d.campaign, str(page - 1)))])
                if len(attempts) > 3:
                    rows.append([(t("ads.next"), cb("history", d.campaign, str(page + 1)))])
                rows.append([(t("ads.back"), cb("campaign", d.campaign))])
                await show(message, service, text, rows)
            elif d.action == "managed":
                page = max(0, min(10000, int(d.value or 0)))
                groups = await global_ads.groups(actor, page=page)
                await service.session.commit()
                rows = []
                for group in groups[:8]:
                    try:
                        await publication_rights(message.bot, group.chat_id, actor)
                    except (DomainError, TelegramAPIError, TimeoutError, OSError):
                        continue
                    rows.append(
                        [(group.title[:48], cb("chat", value=base36(group.chat_id) + ".0"))]
                    )
                if page:
                    rows.append([(t("ads.back"), cb("managed", value=str(page - 1)))])
                if len(groups) > 8:
                    rows.append([(t("ads.next"), cb("managed", value=str(page + 1)))])
                rows.append([(t("ads.back"), cb("root"))])
                await show(message, service, t("g.manage_groups"), rows)
            elif d.action == "chat":
                chat_text, page_text = d.value.split(".")
                chat, page = int(chat_text, 36), max(0, min(10000, int(page_text)))
                group = await PublicationService(service.settings, service.session).group(chat)
                records = list(
                    (
                        await service.session.execute(
                            select(AdSchedule.id, AdCampaign.id, AdCampaign.internal_name)
                            .join(AdCampaign, AdCampaign.id == AdSchedule.campaign_id)
                            .where(
                                AdSchedule.chat_id == chat,
                                AdSchedule.excluded.is_(False),
                                AdCampaign.status == "DRAFT",
                            )
                            .order_by(AdSchedule.id)
                            .offset(page * 8)
                            .limit(9)
                        )
                    ).all()
                )
                text = escape(group.title) + f"\n<code>{chat}</code>"
                await service.session.commit()
                await publication_rights(message.bot, chat, actor)
                rows = [
                    [(name[:48], schedule_cb("panel", cid, sid=sid))]
                    for sid, cid, name in records[:8]
                ]
                if page:
                    rows.append(
                        [(t("ads.back"), cb("chat", value=chat_text + "." + str(page - 1)))]
                    )
                if len(records) > 8:
                    rows.append(
                        [(t("ads.next"), cb("chat", value=chat_text + "." + str(page + 1)))]
                    )
                rows.append([(t("ads.back"), cb("managed"))])
                await show(message, service, text, rows)
            else:
                raise DomainError("ap_stale")
        except (
            DomainError,
            ValueError,
            IndexError,
            TypeError,
            KeyError,
            TelegramAPIError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            if acknowledged and isinstance(callback.message, Message):
                await display(
                    callback.message,
                    failure(error),
                    keyboard([[(t("ads.back"), cb("root"))]]),
                    edit=True,
                )
            else:
                await callback.answer(failure(error), show_alert=True)


async def defaults_panel(message, service, actor, cid):
    policy = await GlobalAdsService(service.settings, service.session).policy(actor, cid)
    text = (
        t("g.defaults_note")
        + "\n\n"
        + t(
            "g.default_values",
            interval=t("ap.i" + str(policy.interval_seconds)),
            start=local(policy.start_at),
            end=local(policy.end_at) if policy.end_at else t("ap.unlimited"),
            mode=t("g.mode_" + policy.mode),
            auto=t("g.yes" if policy.auto_activate else "g.no"),
        )
    )
    if policy.last_error:
        text += "\n" + escape(policy.last_error)
    rows = [
        [(t("ap.intervals"), cb("di", cid))],
        [
            (t("ap.start_date"), cb("de", cid, f"{policy.version}.start_at")),
            (t("ap.end_at"), cb("de", cid, f"{policy.version}.end_at")),
        ],
        [(t("g.apply"), cb("apply", cid))],
    ]
    if policy.mode == "FUTURE":
        rows.append(
            [
                (
                    t("g.future_off" if policy.auto_activate else "g.future_on"),
                    cb("future", cid, "FUTURE_OFF" if policy.auto_activate else "FUTURE_ON"),
                )
            ]
        )
    rows.append([(t("ads.back"), cb("campaign", cid))])
    await show(message, service, text, rows)

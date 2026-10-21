"""Unified private native AUTO ADS navigation, backed by existing per-ad services."""

import re
from copy import deepcopy
from datetime import datetime, timedelta
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, CopyTextButton, InlineKeyboardButton, Message
from sqlalchemy import select

from app.ads_content import parse_buttons
from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_invitations import ClientInvitations
from app.ads_publication import publication_rights
from app.ads_schedule import INTERVALS, local_datetime
from app.ads_unified import UnifiedAds
from app.bot.ads import callback_request, customer_cb, draft_panel, private
from app.bot.ads import cb as content_cb
from app.bot.ads import failure as content_failure
from app.bot.callbacks import (
    AdGroupPublication,
    AdPublication,
    AdsAction,
    AutoAd,
    CustomerAdmin,
    GlobalAd,
)
from app.bot.keyboards import action, keyboard
from app.bot.local_admin import bound_bot, display
from app.bot.states import AdsFlow, AutoAdsFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.i18n import t
from app.locales.auto_ads import EVENTS
from app.models import AdContentDraft, User, now
from app.moderation_services import utc

BRAND = "🛡 <b>CRIMSON SAFECHECK™</b>"


def failure(error):
    if isinstance(error, DomainError) and error.code.startswith("ap_"):
        from app.bot.ads_schedule import failure as publication_failure

        return publication_failure(error)
    return content_failure(error)


def cb(name, cid=0, version=0, value=""):
    return AutoAd(action=name, ad=cid, version=version, value=str(value)).pack()


def title(text):
    return BRAND + "\n\n<b>" + text + "</b>"


def excerpt(value, limit=180):
    result = ""
    for character in str(value):
        encoded = escape(character)
        if len(result) + len(encoded) > limit:
            return result + "…"
        result += encoded
    return result


async def pending(ads, actor, cid):
    await ads.get(actor, cid)
    return list(
        await ads.session.scalars(
            select(AdContentDraft)
            .where(
                AdContentDraft.campaign_id == cid,
                AdContentDraft.actor_id == actor,
                AdContentDraft.status.in_(("READY", "COLLECTING")),
                AdContentDraft.expires_at > now(),
            )
            .order_by(AdContentDraft.created_at.desc())
            .limit(20)
        )
    )


def interval(seconds):
    return t("ua.minute", n=seconds // 60) if seconds < 3600 else t("ua.hour", n=seconds // 3600)


def date(value):
    return utc(value).strftime("%Y-%m-%d %H:%M UTC") if value else "—"


def common(rows, field, default):
    values = {getattr(r, field) for r in rows}
    return next(iter(values)) if len(values) == 1 else default if not values else "MIXED"


def live(rows):
    return any(
        r.status == "ACTIVE" and not r.excluded and (not r.end_at or utc(r.end_at) > now())
        for r in rows
    )


def manager(ads):
    return UnifiedAds(ads.access.settings, ads.session)


def back(cid):
    return [[(t("ua.back"), cb("panel", cid))]]


async def client_label(session, grant):
    if not grant or grant.status != "ACTIVE":
        return t("ua.none")
    user = await session.scalar(select(User).where(User.telegram_id == grant.customer_id))
    return "@" + user.username if user and user.username else str(grant.customer_id)


async def root(message, ads, actor, *, edit=True):
    total, active, paused = await manager(ads).counts(actor)
    await ads.session.commit()
    await display(
        message,
        title("📢 AUTO ADS")
        + "\n\n"
        + t("ua.root")
        + "\n\n"
        + t("ua.counts", total=total, active=active, paused=paused),
        keyboard(
            [
                [(t("ua.new"), content_cb("new"))],
                [(t("ua.mine"), cb("list"))],
                [(t("ua.back"), action("admin"))],
            ]
        ),
        edit=edit,
    )


async def listing(message, ads, actor, state, *, page=0, status=None, edit=True):
    data = await state.get_data()
    status = status or data.get("ua_filter", "all")
    query = data.get("ua_query", "")
    await state.update_data(ua_filter=status)
    management = manager(ads)
    campaigns = await management.listing(actor, status=status, query=query, page=page)
    total, active, paused = await management.counts(actor)
    rows = []
    descriptions = []
    for campaign in campaigns[:4]:
        policy = await management.policy(actor, campaign.id)
        schedules = [
            r
            for r in await management.schedules(campaign.id)
            if not r.excluded and r.chat_id is not None
        ]
        grant = await management.grants.latest(campaign.id)
        seconds = common(schedules, "interval_seconds", policy.interval_seconds)
        duration = (
            t("ua.mixed") if seconds == "MIXED" else interval(seconds or policy.interval_seconds)
        )
        customer = await client_label(ads.session, grant)
        summary = (
            f"{t('ua.active') if live(schedules) else t('ua.paused')} · {duration} · {customer}"
        )
        rows.append([(campaign.internal_name[:64], cb("panel", campaign.id))])
        descriptions.append(f"<b>{escape(campaign.internal_name)}</b>\n{escape(summary)}")
    text = (
        title(t("ua.mine"))
        + "\n\n"
        + t("ua.choose")
        + "\n\n"
        + t("ua.counts", total=total, active=active, paused=paused)
    )
    if query:
        text += "\n⌕ " + escape(query)
    text += "\n\n" + ("\n\n".join(descriptions) if descriptions else t("ua.empty"))
    rows.append(
        [
            (t("ua.all"), cb("filter", value="all")),
            (t("ua.active"), cb("filter", value="active")),
            (t("ua.paused"), cb("filter", value="paused")),
        ]
    )
    rows.append([(t("ua.search"), cb("search"))])
    pages = []
    if page:
        pages.append(("◀", cb("list", value=page - 1)))
    if len(campaigns) > 4:
        pages.append(("▶", cb("list", value=page + 1)))
    if pages:
        rows.append(pages)
    rows += [[(t("ua.new"), content_cb("new"))], [(t("ua.back"), cb("root"))]]
    await ads.session.commit()
    await display(message, text, keyboard(rows), edit=edit)


async def panel(message, ads, actor, cid, *, edit=True):
    if isinstance(ads, CustomerAdsService):
        return await client_panel(message, ads, actor, cid, edit=edit)
    campaign, content, policy, schedules, grant, stats = await manager(ads).details(actor, cid)
    seconds = common(schedules, "interval_seconds", policy.interval_seconds)
    duration = t("ua.mixed") if seconds == "MIXED" else interval(seconds or policy.interval_seconds)
    until = common(schedules, "end_at", policy.end_at)
    end = t("ua.mixed") if until == "MIXED" else date(until) if until else t("ua.unlimited")
    state = t("ua.active") if live(schedules) else t("ua.paused")
    customer = await client_label(ads.session, grant)
    group_count = str(len(schedules))
    if policy.mode == "EXISTING":
        group_count = t("ua.all_snapshot") + f" · {len(schedules)}"
    text = (
        title("📢 " + escape(campaign.internal_name))
        + "\n\n"
        + state
        + "\n\n"
        + t(
            "ua.details",
            client=escape(customer),
            interval=escape(duration),
            groups=group_count,
            end=end,
            sent=stats["counts"].get("SENT", 0),
            buttons=sum(b["kind"] == "url" for row in content.content["buttons"] for b in row),
        )
    )
    cid, version = campaign.id, policy.version
    content_version = campaign.version
    text += "\n\n" + t(
        "ads.content_status",
        text="✓" if content.content["text"] else "×",
        media="✓" if content.content["media"] else "×",
        buttons="✓" if content.content["buttons"] else "×",
    )
    rows = [
        [
            (t("ua.text"), content_cb("qe", cid, f"TEXT.{content_version}")),
            (t("ads.see"), content_cb("qp", cid, "TEXT")),
        ],
        [
            (t("ua.media"), content_cb("qe", cid, f"MEDIA.{content_version}")),
            (t("ads.see"), content_cb("qp", cid, "MEDIA")),
        ],
        [(t("ua.buttons"), cb("buttons", cid)), (t("ads.see"), content_cb("qp", cid, "BUTTONS"))],
        [(t("ua.interval"), cb("interval", cid))],
        [(t("ua.groups"), cb("groups", cid)), (t("ua.client"), cb("client", cid))],
        [(t("ua.link"), cb("links", cid)), (t("ua.validity"), cb("validity", cid))],
        [(t("ua.stats"), cb("stats", cid)), (t("ua.history"), cb("history", cid))],
        [
            (t("ua.preview"), content_cb("qp", cid, "FULL")),
            (
                t("ua.pause") if live(schedules) else t("ua.resume"),
                cb("pause" if live(schedules) else "start", cid, version),
            ),
        ],
    ]
    if not live(schedules):
        rows.append([(t("ua.save_draft"), cb("draft", cid))])
    elif any(r.status != "ACTIVE" for r in schedules):
        rows.append([(t("ua.resume"), cb("start", cid, version))])
    if await pending(ads, actor, cid):
        rows.append([(t("ads.drafts"), cb("drafts", cid))])
    rows += [[(t("ua.delete"), cb("delete", cid, version))], [(t("ua.to_list"), cb("list"))]]
    await ads.session.commit()
    return await display(message, text, keyboard(rows), edit=edit)


async def client_panel(message, ads, actor, cid, *, edit=True):
    campaign, content = await ads.current(actor, cid)
    grant = await ads.grants.check(actor, cid)
    editable = campaign.status == "DRAFT" and (
        not grant.editing_until or utc(grant.editing_until) > now()
    )
    rows = []
    if editable:
        rows = [
            [
                (t("ua.text"), customer_cb("qe", cid, f"TEXT.{campaign.version}")),
                (t("ads.see"), customer_cb("qp", cid, "TEXT")),
            ],
            [
                (t("ua.media"), customer_cb("qe", cid, f"MEDIA.{campaign.version}")),
                (t("ads.see"), customer_cb("qp", cid, "MEDIA")),
            ],
            [
                (t("ua.buttons"), cb("c_buttons", cid)),
                (t("ads.see"), customer_cb("qp", cid, "BUTTONS")),
            ],
            [(t("ua.preview"), customer_cb("qp", cid, "FULL"))],
        ]
        drafts = await ads.unfinished(actor)
        rows += [
            [(t("ads.save"), customer_cb("draft", cid, d.id))]
            for d in drafts
            if d.campaign_id == cid
        ][:3]
    else:
        rows.append([(t("ua.preview"), customer_cb("qp", cid, "FULL"))])
    rows.append([(t("ua.back"), customer_cb("list"))])
    markup = keyboard(rows)
    if editable and await ClientInvitations(ads.access.settings, ads.session).editor_enabled(grant):
        await ads.session.commit()
        me = await bound_bot(message).me()
        if me.username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", me.username):
            url = f"https://t.me/{me.username}?start=ae_{cid}_{grant.id}_{grant.version}"
            markup.inline_keyboard.insert(
                -1,
                [
                    InlineKeyboardButton(
                        text=t("ua.editor_link"), copy_text=CopyTextButton(text=url)
                    )
                ],
            )
    await ads.session.commit()
    return await display(
        message,
        title(t("ua.client_editor"))
        + "\n\n<b>"
        + escape(campaign.internal_name)
        + "</b>\n\n"
        + t("ua.client_note")
        + "\n\n"
        + t(
            "ads.content_status",
            text="✓" if content.content["text"] else "×",
            media="✓" if content.content["media"] else "×",
            buttons="✓" if content.content["buttons"] else "×",
        )
        + ("\n\n" + t("ac.readonly") if not editable else ""),
        markup,
        edit=edit,
    )


async def buttons_panel(message, ads, actor, cid, *, choice=""):
    campaign, version = await ads.current(actor, cid)
    customer = isinstance(ads, CustomerAdsService)
    prefix = "c_" if customer else ""
    rows = []
    text = title(t("ua.buttons"))
    if choice:
        if choice not in {"edit", "remove", "order"}:
            raise DomainError("ads_stale")
        if choice == "order":
            text += "\n\n" + t("ua.button_order_note")
        index = 0
        for row in version.content["buttons"]:
            for button in row:
                if button["kind"] == "url":
                    rows.append(
                        [
                            (
                                button["label"],
                                (customer_cb if customer else content_cb)(
                                    "qe" if choice == "edit" else "qk",
                                    cid,
                                    f"{choice.upper()}.{campaign.version}.{index}",
                                ),
                            )
                        ]
                    )
                index += 1
        rows.append([(t("ua.back"), cb(prefix + "buttons", cid))])
    else:
        rows = [
            [
                (
                    t("ua.button_add"),
                    (customer_cb if customer else content_cb)("qe", cid, f"ADD.{campaign.version}"),
                )
            ],
            [
                (t("ua.button_edit"), cb(prefix + "b_choose", cid, value="edit")),
                (t("ua.button_remove"), cb(prefix + "b_choose", cid, value="remove")),
            ],
            [(t("ua.button_order"), cb(prefix + "b_choose", cid, value="order"))],
            [
                (
                    t("ua.button_bulk"),
                    (customer_cb if customer else content_cb)(
                        "qe", cid, f"BUTTONS.{campaign.version}"
                    ),
                )
            ],
            [(t("ua.preview"), (customer_cb if customer else content_cb)("qp", cid, "FULL"))],
            [(t("ua.back"), (customer_cb("campaign", cid) if customer else cb("panel", cid)))],
        ]
        descriptions: list[str] = []
        for row in version.content["buttons"]:
            for button in row:
                if button["kind"] == "url":
                    line = excerpt(button["label"], 120) + " · " + excerpt(button["value"], 200)
                    if sum(map(len, descriptions)) + len(line) < 2500:
                        descriptions.append(line)
        text += "\n\n" + "\n".join(descriptions)
    await ads.session.commit()
    await display(message, text, keyboard(rows), edit=True)


async def groups_panel(message, management, actor, cid, state, *, page=0):
    policy = await management.policy(actor, cid)
    groups = await management.eligible(actor)
    data = await state.get_data()
    if data.get("ua_group_ad") != cid or data.get("ua_group_version") != policy.version:
        selected = [
            r.chat_id
            for r in await management.schedules(cid)
            if not r.excluded and r.chat_id is not None
        ]
        await state.update_data(
            ua_group_ad=cid, ua_group_version=policy.version, ua_selected=selected
        )
    else:
        selected = data.get("ua_selected", [])
    rows = [
        [
            (
                ("✓ " if g.chat_id in selected else "☐ ") + g.title[:48],
                cb("toggle_group", cid, policy.version, f"{g.chat_id}.{page}"),
            )
        ]
        for g in groups[page * 8 : page * 8 + 8]
    ]
    pages = []
    if page:
        pages.append(("◀", cb("select_groups", cid, value=page - 1)))
    if len(groups) > (page + 1) * 8:
        pages.append(("▶", cb("select_groups", cid, value=page + 1)))
    if pages:
        rows.append(pages)
    rows += [[(t("ua.apply_groups"), cb("apply_groups", cid, policy.version))]] + back(cid)
    await management.session.commit()
    await display(
        message,
        title(t("ua.groups")) + f"\n\n✓ {len(selected)}\n\n" + t("ua.group_note"),
        keyboard(rows),
        edit=True,
    )


async def prompt(message, state, cid, version, field, text, *, session, **extra):
    await session.commit()
    await state.set_state(AutoAdsFlow.input)
    await state.update_data(
        ua_ad=cid, ua_version=version, ua_field=field, ua_panel=message.message_id, **extra
    )
    await display(
        message,
        title("📢 AUTO ADS") + "\n\n" + text,
        keyboard(back(cid) if cid else [[(t("ua.back"), cb("list"))]]),
        edit=True,
    )


async def link_panel(message, access, actor, cid):
    grant = await access.admin(actor, cid)
    stamp = access.stamp(grant)
    rows = [[(t("ua.generate"), cb("generate", cid, value=stamp))]]
    markup = keyboard(rows + [[(t("ua.unlink"), cb("unlink", cid, value=stamp))]] + back(cid))
    if (
        grant
        and grant.status == "ACTIVE"
        and await ClientInvitations(access.ads.access.settings, access.session).editor_enabled(
            grant
        )
    ):
        await access.session.commit()
        me = await bound_bot(message).me()
        if not me.username or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", me.username):
            raise DomainError("ads_stale")
        url = f"https://t.me/{me.username}?start=ae_{cid}_{grant.id}_{grant.version}"
        markup.inline_keyboard.insert(
            1, [InlineKeyboardButton(text=t("ua.editor_link"), copy_text=CopyTextButton(text=url))]
        )
    await access.session.commit()
    await display(
        message, title(t("ua.link_title")) + "\n\n" + t("ua.editor_note"), markup, edit=True
    )


async def open_link(message, state, service, payload):
    actor = actor_id(message)
    try:
        if message.chat.type != "private" or message.chat.id != actor:
            raise DomainError("ac_denied")
        links = ClientInvitations(service.settings, service.session)
        cid = await (
            links.consume(actor, payload)
            if payload.startswith("ai_")
            else links.editor(actor, payload)
        )
        ads = CustomerAdsService(service.settings, service.session)
        from app.bot.ads_message_editor import leave_editor

        await leave_editor(message, state)
        await state.clear()
        await client_panel(message, ads, actor, cid, edit=False)
    except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError):
        await service.session.rollback()
        await message.answer(t("ua.expired_link"), parse_mode="HTML")


def register_auto_ads_handlers(router: Router):
    # Cached management buttons re-enter the unified UI; old future-enrolment
    # commands can never re-enable an unapproved expanding audience.
    @router.callback_query(AdsAction.filter(F.action.in_({"archive", "delete"})))
    @router.callback_query(GlobalAd.filter())
    @router.callback_query(AdPublication.filter())
    @router.callback_query(AdGroupPublication.filter())
    @router.callback_query(CustomerAdmin.filter())
    async def legacy(callback: CallbackQuery, callback_data, state: FSMContext, service):
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            ads = await private(service, callback.message, callback.from_user.id)
            from app.bot.ads_message_editor import leave_editor

            await leave_editor(callback.message, state)
            await state.set_state(None)
            await service.session.commit()
            await callback.answer()
            cid = getattr(callback_data, "campaign", 0)
            if isinstance(callback_data, GlobalAd) and callback_data.action == "new":
                await state.set_state(AdsFlow.input)
                await state.update_data(
                    ads_field="NAME",
                    ads_panel=callback.message.message_id,
                    ads_customer=False,
                    global_new=False,
                )
                await display(
                    callback.message,
                    t("ads.name_prompt"),
                    keyboard([[(t("ua.back"), cb("root"))]]),
                    edit=True,
                )
            elif cid:
                await panel(callback.message, ads, callback.from_user.id, cid)
            else:
                await root(callback.message, ads, callback.from_user.id)
        except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await callback.answer(failure(error), show_alert=True)

    @router.callback_query(AdsAction.filter(F.action == "list"))
    async def old_list(
        callback: CallbackQuery, callback_data: AdsAction, state: FSMContext, service
    ):
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            ads = await private(service, callback.message, callback.from_user.id)
            await state.set_state(None)
            await service.session.commit()
            await callback.answer()
            await listing(callback.message, ads, callback.from_user.id, state)
        except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await callback.answer(failure(error), show_alert=True)

    @router.message(AutoAdsFlow.input, ~F.text.startswith("/"))
    async def input_value(message: Message, state: FSMContext, service):
        try:
            actor = actor_id(message)
            data = await state.get_data()
            field, cid = data["ua_field"], int(data["ua_ad"])
            customer = data.get("ua_customer") is True and field in {"b_add", "b_edit"}
            ads = await private(service, message, actor, customer=customer)
            management = manager(ads)
            text = message.text or ""
            menu = message.model_copy(update={"message_id": data["ua_panel"]}).as_(message.bot)
            if field == "search":
                if not text.strip() or len(text.strip()) > 128:
                    raise DomainError("ads_name")
                await state.update_data(ua_query=text.strip())
                await state.set_state(None)
                await listing(menu, ads, actor, state, page=0)
                return
            if field in {"b_add", "b_edit"}:
                campaign, version = await ads.current(actor, cid)
                if campaign.version != data["ua_version"]:
                    raise DomainError("ads_conflict")
                if isinstance(ads, CustomerAdsService):
                    ads.expected_grant = int(data["ua_grant"])
                parsed = parse_buttons(text)
                if len(parsed) != 1 or len(parsed[0]) != 1 or parsed[0][0]["kind"] != "url":
                    raise DomainError("ads_buttons")
                buttons = deepcopy(version.content["buttons"])
                if field == "b_add":
                    buttons.append(parsed[0])
                else:
                    locations = [(r, c) for r, row in enumerate(buttons) for c in range(len(row))]
                    r, c = locations[int(data["ua_index"])]
                    if buttons[r][c]["kind"] != "url":
                        raise DomainError("ads_stale")
                    buttons[r][c] = parsed[0][0]
                draft = await ads.stage(
                    actor,
                    cid,
                    campaign.version,
                    "BUTTONS",
                    buttons,
                    f"ads:{message.chat.id}:{message.message_id}",
                )
                await state.set_state(None)
                await draft_panel(menu, ads, actor, draft.id)
                return
            if field == "client_deadline":
                deadline = None if text.strip() == "0" else local_datetime(text)
                await CustomerAccess(service.settings, service.session).deadline(
                    actor, cid, data["ua_stamp"], deadline
                )
            elif field == "client":
                if not re.fullmatch(r"[1-9][0-9]{0,18}", text):
                    raise DomainError("ads_stale")
                await CustomerAccess(service.settings, service.session).assign(
                    actor, cid, int(text), data["ua_stamp"]
                )
            else:
                value: datetime | None
                if field == "extend":
                    if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= 3650:
                        raise DomainError("ap_time")
                    policy = await management.policy(actor, cid)
                    ends = [
                        utc(r.end_at)
                        for r in await management.schedules(cid)
                        if r.end_at and not r.excluded
                    ]
                    if policy.end_at:
                        ends.append(utc(policy.end_at))
                    value = max([now(), *ends]) + timedelta(days=int(text))
                    field = "end_at"
                else:
                    value = (
                        None if field == "end_at" and text.strip() == "0" else local_datetime(text)
                    )
                await management.change(actor, cid, data["ua_version"], field, value, message.bot)
            await state.set_state(None)
            await panel(menu, ads, actor, cid)
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            IndexError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.callback_query(AutoAd.filter())
    async def handle(callback: CallbackQuery, callback_data: AutoAd, state: FSMContext, service):
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            message, actor = callback.message, callback.from_user.id
            name, cid, expected, value = (
                callback_data.action,
                callback_data.ad,
                callback_data.version,
                callback_data.value,
            )
            customer = name.startswith("c_")
            name = name.removeprefix("c_")
            if customer and name not in {
                "buttons",
                "b_choose",
                "b_add",
                "b_edit",
                "b_remove",
                "b_order",
            }:
                raise DomainError("ac_denied")
            ads = await private(service, message, actor, customer=customer)
            management = manager(ads)
            from app.bot.ads_message_editor import leave_editor

            await leave_editor(message, state)
            if cid:
                await ads.get(actor, cid)
            await state.set_state(None)
            await service.session.commit()
            await callback.answer()
            acknowledged = True
            if name == "root":
                await state.update_data(ua_query="", ua_filter="all")
                await root(message, ads, actor)
            elif name in {"list", "filter"}:
                if name == "filter":
                    await state.update_data(ua_query="")
                await listing(
                    message,
                    ads,
                    actor,
                    state,
                    page=max(0, int(value or "0")) if name == "list" else 0,
                    status=value if name == "filter" else None,
                )
            elif name == "search":
                await prompt(
                    message,
                    state,
                    0,
                    0,
                    "search",
                    t("ua.search_prompt"),
                    ua_customer=False,
                    session=service.session,
                )
            elif name == "panel":
                await panel(message, ads, actor, cid)
            elif name == "drafts":
                drafts = await pending(ads, actor, cid)
                rows = [
                    [
                        (
                            t("ads." + d.field.lower())
                            if d.field != "RESTORE"
                            else t("ads.restore"),
                            content_cb("draft", cid, d.id),
                        )
                    ]
                    for d in drafts
                ]
                await service.session.commit()
                await display(
                    message, title(t("ads.drafts")), keyboard(rows + back(cid)), edit=True
                )
            elif name == "draft":
                await ads.get(actor, cid)
                await service.session.commit()
                await display(message, t("ua.saved_draft"), keyboard(back(cid)), edit=True)
            elif name in {"buttons", "b_choose"}:
                await buttons_panel(
                    message, ads, actor, cid, choice=value if name == "b_choose" else ""
                )
            elif name in {"b_add", "b_edit", "b_remove", "b_order"}:
                campaign, version = await ads.current(actor, cid)
                if campaign.version != expected:
                    raise DomainError("ads_conflict")
                grant = (
                    await ads.grants.check(actor, cid, edit=True)
                    if isinstance(ads, CustomerAdsService)
                    else None
                )
                if name in {"b_add", "b_edit"}:
                    await prompt(
                        message,
                        state,
                        cid,
                        expected,
                        name,
                        t("ua.button_prompt"),
                        ua_customer=customer,
                        session=service.session,
                        ua_grant=grant.id if grant else 0,
                        ua_index=value or "0",
                    )
                else:
                    buttons = deepcopy(version.content["buttons"])
                    locations = [(r, c) for r, row in enumerate(buttons) for c in range(len(row))]
                    index = int(value)
                    if not 0 <= index < len(locations):
                        raise DomainError("ads_stale")
                    r, c = locations[index]
                    if buttons[r][c]["kind"] != "url":
                        raise DomainError("ads_stale")
                    if name == "b_remove":
                        buttons[r].pop(c)
                        buttons = [row for row in buttons if row]
                    else:
                        r2, c2 = locations[(index - 1) % len(locations)]
                        buttons[r][c], buttons[r2][c2] = buttons[r2][c2], buttons[r][c]
                    draft = await ads.stage(
                        actor,
                        cid,
                        expected,
                        "BUTTONS",
                        buttons,
                        callback_request(callback, "buttons"),
                    )
                    await draft_panel(message, ads, actor, draft.id)
            elif name == "interval":
                policy = await management.policy(actor, cid)
                rows = [
                    [
                        (interval(n), cb("set_interval", cid, policy.version, n))
                        for n in INTERVALS[i : i + 2]
                    ]
                    for i in range(0, len(INTERVALS), 2)
                ]
                await service.session.commit()
                await display(
                    message, title(t("ua.interval")), keyboard(rows + back(cid)), edit=True
                )
            elif name == "set_interval":
                await management.change(
                    actor, cid, expected, "interval_seconds", int(value), message.bot
                )
                await panel(message, ads, actor, cid)
            elif name == "groups":
                await display(
                    message,
                    title(t("ua.groups")) + "\n\n" + t("ua.group_note"),
                    keyboard(
                        [
                            [(t("ua.all_groups"), cb("all_groups", cid))],
                            [(t("ua.select_groups"), cb("select_groups", cid))],
                        ]
                        + back(cid)
                    ),
                    edit=True,
                )
            elif name == "select_groups":
                await groups_panel(
                    message, management, actor, cid, state, page=max(0, int(value or 0))
                )
            elif name == "all_groups":
                policy = await management.policy(actor, cid)
                groups = await management.eligible(actor)
                await service.session.commit()
                selected = []
                for group in groups:
                    try:
                        await publication_rights(message.bot, group.chat_id, actor)
                        selected.append(group.chat_id)
                    except TelegramForbiddenError:
                        continue
                    except DomainError as error:
                        if error.code != "ap_rights":
                            raise
                if not selected:
                    raise DomainError("ap_group")
                await state.update_data(
                    ua_group_ad=cid, ua_group_version=policy.version, ua_selected=selected
                )
                await display(
                    message,
                    title(t("ua.all_groups")) + f"\n\n✓ {len(selected)}\n\n" + t("ua.group_note"),
                    keyboard(
                        [[(t("ua.apply_groups"), cb("apply_groups", cid, policy.version, "all"))]]
                        + back(cid)
                    ),
                    edit=True,
                )
            elif name == "toggle_group":
                chat, page = map(int, value.split("."))
                data = await state.get_data()
                if data.get("ua_group_ad") != cid or data.get("ua_group_version") != expected:
                    raise DomainError("ads_stale")
                selection = set(data.get("ua_selected", []))
                selection.symmetric_difference_update({chat})
                await state.update_data(ua_selected=sorted(selection))
                await groups_panel(message, management, actor, cid, state, page=page)
            elif name == "apply_groups":
                data = await state.get_data()
                if data.get("ua_group_ad") != cid or data.get("ua_group_version") != expected:
                    raise DomainError("ads_stale")
                await management.groups(
                    actor,
                    cid,
                    expected,
                    data.get("ua_selected", []),
                    message.bot,
                    all_groups=value == "all",
                )
                await state.update_data(ua_group_ad=0)
                await panel(message, ads, actor, cid)
            elif name == "validity":
                _, _, policy, schedules, _, _ = await management.details(actor, cid)
                start = common(schedules, "start_at", policy.start_at)
                end = common(schedules, "end_at", policy.end_at)
                text = t(
                    "ua.validity_view",
                    start=t("ua.mixed") if start == "MIXED" else date(start),
                    end=t("ua.mixed") if end == "MIXED" else date(end),
                )
                await service.session.commit()
                await display(
                    message,
                    title(t("ua.validity")) + "\n\n" + text,
                    keyboard(
                        [
                            [
                                (t("ua.start"), cb("set_date", cid, policy.version, "start_at")),
                                (t("ua.end"), cb("set_date", cid, policy.version, "end_at")),
                            ],
                            [(t("ua.extend"), cb("set_date", cid, policy.version, "extend"))],
                            [(t("ua.preview"), cb("validity", cid))],
                        ]
                        + back(cid)
                    ),
                    edit=True,
                )
            elif name == "set_date":
                if value not in {"start_at", "end_at", "extend"}:
                    raise DomainError("ads_stale")
                await prompt(
                    message,
                    state,
                    cid,
                    expected,
                    value,
                    t("ua.extend_prompt" if value == "extend" else "ua.date_prompt"),
                    ua_customer=False,
                    session=service.session,
                )
            elif name == "client":
                access = CustomerAccess(service.settings, service.session)
                grant = await access.admin(actor, cid)
                stamp = access.stamp(grant)
                label = await client_label(service.session, grant)
                await service.session.commit()
                await display(
                    message,
                    title(t("ua.client")) + "\n\n" + escape(label),
                    keyboard(
                        [
                            [(t("ua.assign"), cb("assign", cid, value=stamp))],
                            [(t("ua.revoke"), cb("revoke", cid, value=stamp))],
                            [(t("ua.client_deadline"), cb("client_date", cid, value=stamp))],
                        ]
                        + back(cid)
                    ),
                    edit=True,
                )
            elif name == "client_date":
                await prompt(
                    message,
                    state,
                    cid,
                    0,
                    "client_deadline",
                    t("ua.date_prompt"),
                    ua_stamp=value,
                    ua_customer=False,
                    session=service.session,
                )
            elif name == "assign":
                await prompt(
                    message,
                    state,
                    cid,
                    0,
                    "client",
                    t("ua.client_prompt"),
                    ua_stamp=value,
                    ua_customer=False,
                    session=service.session,
                )
            elif name == "revoke":
                await display(
                    message,
                    t("ua.confirm_revoke"),
                    keyboard(
                        [
                            [(t("ua.confirm"), cb("do_revoke", cid, value=value))],
                        ]
                        + back(cid)
                    ),
                    edit=True,
                )
            elif name == "do_revoke":
                await CustomerAccess(service.settings, service.session).revoke(actor, cid, value)
                await ClientInvitations(service.settings, service.session).revoke(actor, cid)
                await panel(message, ads, actor, cid)
            elif name == "links":
                await link_panel(
                    message, CustomerAccess(service.settings, service.session), actor, cid
                )
            elif name == "generate":
                me = await bound_bot(message).me()
                if not me.username or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", me.username):
                    raise DomainError("ads_stale")
                payload = await ClientInvitations(service.settings, service.session).issue(
                    actor, cid, value
                )
                url = f"https://t.me/{me.username}?start={payload}"
                markup = keyboard(back(cid))
                markup.inline_keyboard.insert(
                    0, [InlineKeyboardButton(text=t("ua.copy"), copy_text=CopyTextButton(text=url))]
                )
                await display(
                    message,
                    title(t("ua.link_title"))
                    + "\n\n"
                    + t("ua.invite_note")
                    + "\n\n<code>"
                    + escape(url)
                    + "</code>",
                    markup,
                    edit=True,
                )
            elif name == "unlink":
                access = CustomerAccess(service.settings, service.session)
                grant = await access.checked_admin(actor, cid, value)
                if grant and grant.status == "ACTIVE":
                    await access.unlink(actor, cid, value)
                await ClientInvitations(service.settings, service.session).revoke(actor, cid)
                await link_panel(message, access, actor, cid)
            elif name == "stats":
                stats = await management.statistics(actor, cid)
                text = t(
                    "ua.stats_view",
                    sent=stats["counts"].get("SENT", 0),
                    failed=stats["counts"].get("FAILED", 0),
                    uncertain=stats["counts"].get("UNCERTAIN", 0),
                    total=stats["total"],
                    last=date(stats["last"]),
                    next=date(stats["next"]),
                    first=date(stats["first"]),
                    groups=(
                        "\n".join(
                            excerpt(g.title, 100) + f" · {g.chat_id}" for g in stats["groups"][:8]
                        )
                        + (
                            "\n" + t("ua.more", n=len(stats["groups"]) - 8)
                            if len(stats["groups"]) > 8
                            else ""
                        )
                    )
                    or "—",
                    errors="\n".join(excerpt(e.error, 180) for e in stats["errors"]) or "—",
                )
                await service.session.commit()
                await display(
                    message,
                    title(t("ua.stats")) + "\n\n" + text,
                    keyboard([[(t("ua.deliveries"), cb("deliveries", cid))]] + back(cid)),
                    edit=True,
                )
            elif name == "deliveries":
                page = max(0, int(value or 0))
                attempts = await management.deliveries(actor, cid, page)
                rows = []
                pages = []
                if page:
                    pages.append(("◀", cb("deliveries", cid, value=page - 1)))
                if len(attempts) > 5:
                    pages.append(("▶", cb("deliveries", cid, value=page + 1)))
                if pages:
                    rows.append(pages)
                text = "\n\n".join(
                    f"#{a.id} · {date(a.due_at)}\n{a.chat_id} · {t('ap.' + a.status)}"
                    + ("\n" + excerpt(a.error, 200) if a.error else "")
                    for a in attempts[:5]
                )
                await service.session.commit()
                await display(
                    message,
                    title(t("ua.deliveries")) + "\n\n" + (text or t("ua.history_empty")),
                    keyboard(rows + back(cid)),
                    edit=True,
                )
            elif name == "history":
                page = max(0, int(value or 0))
                events = await management.history(actor, cid, page)
                entries = []
                for event in events[:5]:
                    details = []
                    field = event.details.get("field")
                    field_keys = {
                        "TEXT": "text",
                        "MEDIA": "media",
                        "BUTTONS": "buttons",
                        "interval_seconds": "interval",
                        "start_at": "start",
                        "end_at": "end",
                        "chat_id": "groups",
                    }
                    if field in field_keys:
                        details.append(t("ua." + field_keys[field]))
                    if event.details.get("revision"):
                        details.append("v" + str(event.details["revision"]))
                    if event.details.get("customer_id"):
                        details.append("👤 " + str(event.details["customer_id"]))
                    if event.details.get("chat_ids"):
                        details.append(t("ua.groups") + f" · {len(event.details['chat_ids'])}")
                    operation = event.details.get("operation")
                    if operation in {"START", "PAUSE", "DELETE"}:
                        details.append(
                            t(
                                "ua."
                                + {"START": "resume", "PAUSE": "pause", "DELETE": "delete"}[
                                    operation
                                ]
                            )
                        )
                    key = event.action.removeprefix("ads_")
                    event_text = t("ua.event_" + key) if key in EVENTS else t("ua.event_other")
                    entries.append(
                        t(
                            "ua.history_row",
                            date=date(event.created_at),
                            actor=event.actor_id,
                            event=event_text,
                            details=" · ".join(details),
                        )
                    )
                rows = []
                if page:
                    rows.append([("◀", cb("history", cid, value=page - 1))])
                if len(events) > 5:
                    rows.append([("▶", cb("history", cid, value=page + 1))])
                await service.session.commit()
                await display(
                    message,
                    title(t("ua.history"))
                    + "\n\n"
                    + ("\n\n".join(entries) or t("ua.history_empty")),
                    keyboard(rows + back(cid)),
                    edit=True,
                )
            elif name in {"start", "pause", "do_delete"}:
                await management.lifecycle(
                    actor,
                    cid,
                    expected,
                    {"start": "START", "pause": "PAUSE", "do_delete": "DELETE"}[name],
                    message.bot,
                )
                if name == "do_delete":
                    await listing(message, ads, actor, state)
                else:
                    await panel(message, ads, actor, cid)
            elif name == "delete":
                await display(
                    message,
                    t("ua.confirm_delete"),
                    keyboard(
                        [
                            [(t("ua.confirm"), cb("do_delete", cid, expected))],
                        ]
                        + back(cid)
                    ),
                    edit=True,
                )
            else:
                raise DomainError("ads_stale")
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            KeyError,
            IndexError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            if acknowledged:
                await message.answer(failure(error), parse_mode="HTML")
            else:
                await callback.answer(failure(error), show_alert=True)

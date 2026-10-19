"""Private native client panels and administrator-managed, one-time editing links."""

from datetime import datetime
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import func, select

from app.ads_customers import CustomerAccess, CustomerAdsService
from app.ads_publication import PublicationService
from app.bot.ads import cb as admin_content_cb
from app.bot.ads import customer_cb, failure, private
from app.bot.callbacks import CustomerAdmin
from app.bot.keyboards import keyboard
from app.bot.local_admin import display
from app.bot.states import AdCustomerFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.i18n import t
from app.models import AdPublicationAttempt, AdSchedule, now
from app.moderation_services import utc
from app.services import Service


def cb(name: str, cid: int, value: str = "") -> str:
    return CustomerAdmin(action=name, campaign=cid, value=value).pack()


def back(cid: int):
    return keyboard([[(t("ads.back"), cb("panel", cid))]])


async def customer_list(message, ads, actor, *, page=0, edit=True):
    campaigns = await ads.campaigns(actor, page)
    # Internal administrative names are never exposed to a customer.
    rows = [[(f"📢 #{c.id}", customer_cb("campaign", c.id))] for c in campaigns[:8]]
    if page:
        rows.append([(t("ads.back"), customer_cb("list", value=str(page - 1)))])
    if len(campaigns) > 8:
        rows.append([(t("ads.next"), customer_cb("list", value=str(page + 1)))])
    drafts = await ads.unfinished(actor)
    if drafts:
        rows.append([(t("ads.drafts"), customer_cb("drafts"))])
    await ads.session.commit()
    await display(
        message,
        t("ac.mine") + ("\n\n" + t("ac.empty") if not campaigns else ""),
        keyboard(rows),
        edit=edit,
    )


async def customer_panel(message, ads, actor, cid, *, edit=True):
    campaign, _ = await ads.current(actor, cid)
    grant = await ads.grants.check(actor, cid)
    editable = campaign.status == "DRAFT" and (
        not grant.editing_until or utc(grant.editing_until) > now()
    )
    rows = []
    if editable:
        rows += [
            [
                (t("ac.text"), customer_cb("edit", cid, "TEXT")),
                (t("ac.media"), customer_cb("edit", cid, "MEDIA")),
            ],
            [
                (t("ac.buttons"), customer_cb("edit", cid, "BUTTONS")),
                (t("ads.preview"), customer_cb("preview", cid, "FULL")),
            ],
        ]
    else:
        rows.append([(t("ads.preview"), customer_cb("preview", cid, "FULL"))])
    rows += [
        [
            (t("ap.stats"), customer_cb("stats", cid)),
            (t("ads.history"), customer_cb("history", cid)),
        ],
        [(t("ac.back"), customer_cb("list"))],
    ]
    text = (
        t("ac.title")
        + "\n\n"
        + t(
            "ac.content",
            id=cid,
            version=campaign.version,
            state=t("ac.readonly") if not editable else "",
        )
    )
    await ads.session.commit()
    await display(message, text, keyboard(rows), edit=edit)


async def customer_stats(message, ads, actor, cid):
    campaign = await ads.get(actor, cid)
    attempts = AdPublicationAttempt
    counts = dict(
        (
            await ads.session.execute(
                select(attempts.status, func.count())
                .join(AdSchedule)
                .where(AdSchedule.campaign_id == cid)
                .group_by(attempts.status)
            )
        ).all()
    )
    partial = await ads.session.scalar(
        select(func.count())
        .select_from(attempts)
        .join(AdSchedule)
        .where(AdSchedule.campaign_id == cid, PublicationService.partial_outcome())
    )
    last = await ads.session.scalar(
        select(func.max(attempts.completed_at))
        .join(AdSchedule)
        .where(AdSchedule.campaign_id == cid, attempts.status == "SENT")
    )
    schedules = list(
        await ads.session.scalars(select(AdSchedule).where(AdSchedule.campaign_id == cid))
    )
    upcoming = [
        v for row in schedules if (v := PublicationService.next_due(row, campaign)) is not None
    ]

    def date(value):
        return utc(value).strftime("%Y-%m-%d %H:%M UTC") if value else "—"

    text = t(
        "ac.stats",
        sent=counts.get("SENT", 0),
        failed=counts.get("FAILED", 0),
        uncertain=counts.get("UNCERTAIN", 0),
        partial=partial or 0,
        last=date(last),
        next=date(min(upcoming) if upcoming else None),
    )
    await ads.session.commit()
    await display(
        message,
        t("ap.stats") + "\n\n" + text,
        keyboard([[(t("ads.back"), customer_cb("campaign", cid))]]),
        edit=True,
    )


async def admin_panel(message, access, actor, cid, *, links=False):
    grant = await access.admin(actor, cid)
    stamp = access.stamp(grant)
    active = grant is not None and grant.status == "ACTIVE"
    text = t("ac.links_title" if links else "ac.manage_title") + "\n\n"
    text += (
        t(
            "ac.grant",
            id=grant.customer_id,
            status=t("ac.active" if active else "ac.revoked"),
            deadline=escape(utc(grant.editing_until).isoformat())
            if grant.editing_until
            else t("ac.unlimited"),
        )
        if grant
        else t("ac.none")
    )
    rows = []
    if links:
        text += "\n\n" + t("ac.once")
        if active:
            rows = [
                [(t("ac.create"), cb("create", cid, stamp))],
                [(t("ac.rotate"), cb("rotate", cid, stamp))],
                [(t("ac.unlink"), cb("unlink", cid, stamp))],
            ]
        rows.append([(t("ads.back"), cb("panel", cid))])
    else:
        rows.append([(t("ac.change" if active else "ac.assign"), cb("assign", cid, stamp))])
        if active:
            rows += [
                [(t("ac.revoke"), cb("revoke", cid, stamp))],
                [(t("ac.deadline"), cb("deadline", cid, stamp))],
            ]
        rows += [
            [(t("ac.access_history"), cb("history", cid))],
            [(t("ac.links"), cb("links", cid))],
            [(t("ads.back"), admin_content_cb("campaign", cid))],
        ]
    await access.session.commit()
    await display(message, text, keyboard(rows), edit=True)


async def open_link(message: Message, state: FSMContext, service: Service, parameter: str) -> None:
    """Consume start payload without persisting it in FSM, audit details or logs."""
    await state.clear()
    try:
        actor = actor_id(message)
        ads = await private(service, message, actor, customer=True)
        assert isinstance(ads, CustomerAdsService)
        cid = await ads.grants.resolve(actor, parameter[3:])
        await customer_panel(message, ads, actor, cid, edit=False)
    except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError):
        await service.session.rollback()
        await message.answer(t("ac.denied"), parse_mode="HTML")


def register_customer_handlers(router: Router) -> None:
    @router.message(Command("myads"))
    async def my_ads(message: Message, state: FSMContext, service: Service):
        try:
            ads = await private(service, message, actor_id(message), customer=True)
            await state.clear()
            await customer_list(message, ads, actor_id(message), edit=False)
        except (DomainError, TelegramAPIError, ValueError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await message.answer(failure(error), parse_mode="HTML")

    @router.message(AdCustomerFlow.input, ~F.text.startswith("/"))
    async def admin_input(message: Message, state: FSMContext, service: Service):
        try:
            actor = actor_id(message)
            await private(service, message, actor)
            data = await state.get_data()
            access = CustomerAccess(service.settings, service.session)
            cid, stamp = int(data["ac_campaign"]), data["ac_stamp"]
            value = (message.text or "").strip()
            if data["ac_field"] == "assign":
                if not value.isascii() or not value.isdecimal():
                    raise DomainError("ads_stale")
                await access.assign(actor, cid, int(value), stamp)
            else:
                until = None if value == "-" else datetime.fromisoformat(value)
                await access.deadline(actor, cid, stamp, until)
            await state.set_state(None)
            panel = message.model_copy(update={"message_id": data["ac_panel"]}).as_(message.bot)
            await admin_panel(panel, access, actor, cid)
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

    @router.callback_query(CustomerAdmin.filter())
    async def admin_callback(
        callback: CallbackQuery, callback_data: CustomerAdmin, state: FSMContext, service: Service
    ):
        acknowledged = False
        try:
            if not isinstance(callback.message, Message):
                raise DomainError("forbidden")
            message, actor = callback.message, callback.from_user.id
            await private(service, message, actor)
            access = CustomerAccess(service.settings, service.session)
            name, cid, value = callback_data.action, callback_data.campaign, callback_data.value
            await access.admin(actor, cid)
            await state.set_state(None)
            await callback.answer()
            acknowledged = True
            if name in {"panel", "links"}:
                await admin_panel(message, access, actor, cid, links=name == "links")
            elif name in {"assign", "deadline"}:
                await access.checked_admin(actor, cid, value)
                await state.set_state(AdCustomerFlow.input)
                await state.update_data(
                    ac_campaign=cid, ac_stamp=value, ac_field=name, ac_panel=message.message_id
                )
                await service.session.commit()
                await display(
                    message,
                    t("ac.id_prompt" if name == "assign" else "ac.date_prompt"),
                    back(cid),
                    edit=True,
                )
            elif name in {"revoke", "unlink", "rotate"}:
                await access.checked_admin(actor, cid, value)
                await service.session.commit()
                await display(
                    message,
                    t("ac.confirm_" + name),
                    keyboard(
                        [
                            [(t("ac.confirm"), cb("do_" + name, cid, value))],
                            [(t("ads.back"), cb("panel", cid))],
                        ]
                    ),
                    edit=True,
                )
            elif name in {"create", "do_rotate"}:
                # Get the public username BEFORE a secret exists; never print API errors with the link.
                await service.session.commit()
                assert message.bot is not None
                me = await message.bot.me()
                if not me.username:
                    raise DomainError("ads_stale")
                token = await access.link(actor, cid, value, rotate=name == "do_rotate")
                url = f"https://t.me/{me.username}?start=ad_{token}"
                markup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=t("ac.copy"), copy_text=CopyTextButton(text=url)
                            )
                        ],
                        [InlineKeyboardButton(text=t("ads.back"), callback_data=cb("links", cid))],
                    ]
                )
                await display(
                    message,
                    t("ac.once") + "\n\n<code>" + escape(url) + "</code>",
                    markup,
                    edit=True,
                )
            elif name == "do_revoke":
                await access.revoke(actor, cid, value)
                await admin_panel(message, access, actor, cid)
            elif name == "do_unlink":
                await access.unlink(actor, cid, value)
                await admin_panel(message, access, actor, cid, links=True)
            elif name == "history":
                page = max(0, min(10000, int(value or 0)))
                rows = await access.history(actor, cid, page)
                text = (
                    t("ac.access_history")
                    + "\n\n"
                    + (
                        "\n".join(
                            f"{utc(r.created_at):%Y-%m-%d %H:%M} · {t('ac.event_' + r.action.removeprefix('ads_customer_'))} · <code>{r.actor_id}</code>"
                            for r in rows[:5]
                        )
                        or t("ac.history_empty")
                    )
                )
                buttons = []
                if page:
                    buttons.append([(t("ads.back"), cb("history", cid, str(page - 1)))])
                if len(rows) > 5:
                    buttons.append([(t("ads.next"), cb("history", cid, str(page + 1)))])
                buttons.append([(t("ads.back"), cb("panel", cid))])
                await service.session.commit()
                await display(message, text, keyboard(buttons), edit=True)
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
                await display(
                    callback.message,
                    failure(error),
                    keyboard([[(t("ads.back"), admin_content_cb("root"))]]),
                    edit=True,
                )

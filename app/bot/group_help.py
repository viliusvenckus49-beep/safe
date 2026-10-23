"""Group-first private settings; every entry rechecks Telegram group authority."""

import asyncio
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.ads_services import AdsService
from app.bot.callbacks import AutoAd, CheckSettings, GroupHelp, LocalAction, WarningSettings
from app.bot.keyboards import action, keyboard
from app.bot.local_admin import access, display
from app.bot.screens import clear_flow
from app.errors import DomainError
from app.group_authorization import GroupAuthorizer
from app.group_services import GroupService
from app.i18n import t
from app.models import AdCampaign, AdSchedule, GroupModerationSettings, GroupSanction, ManagedGroup


def cb(name="groups", chat=0, page=0):
    return GroupHelp(action=name, chat=chat, page=page).pack()


async def groups(message, service, actor, page=0, *, history=False):
    entries = list(
        await service.session.scalars(
            select(ManagedGroup)
            .where(
                (
                    (ManagedGroup.approved.is_(True) & ManagedGroup.enabled.is_(True))
                    | select(GroupModerationSettings.chat_id)
                    .where(
                        GroupModerationSettings.chat_id == ManagedGroup.chat_id,
                        GroupModerationSettings.local_auto_enabled.is_(True),
                    )
                    .exists()
                ),
                ~GroupService.removed_group(ManagedGroup.chat_id),
            )
            .order_by(ManagedGroup.chat_id)
        )
    )
    # End the read transaction before concurrent Telegram I/O.
    await service.session.commit()
    semaphore = asyncio.Semaphore(4)

    async def permitted(group):
        async with semaphore:
            try:
                await GroupAuthorizer(message.bot).actor(group.chat_id, actor, configure=True)
                return True
            except (DomainError, TelegramAPIError, TimeoutError, OSError):
                return False

    allowed = [
        g
        for g, ok in zip(
            entries, await asyncio.gather(*(permitted(g) for g in entries)), strict=True
        )
        if ok
    ]
    page = min(page, max(0, (len(allowed) - 1) // 5))
    rows = [
        [(g.title[:55], cb("history" if history else "group", g.chat_id))]
        for g in allowed[page * 5 : page * 5 + 5]
    ]
    pages = []
    if page:
        pages.append(("◀", cb("history_groups" if history else "groups", page=page - 1)))
    if len(allowed) > page * 5 + 5:
        pages.append(("▶", cb("history_groups" if history else "groups", page=page + 1)))
    if pages:
        rows.append(pages)
    rows.append([(t("button.back"), cb("root"))])
    await display(
        message,
        t("gh.title") + "\n\n" + t("lm.groups" if allowed else "lm.empty"),
        keyboard(rows),
        edit=True,
    )


async def panel(message, service, actor, chat):
    local = await access(service, message.bot, chat, actor, configure=True)
    await local.ensure_commands(chat, actor)
    group = await service.session.get(ManagedGroup, chat)
    rows = [
        [(t("nav.moderation"), LocalAction(action="group", chat=chat).pack())],
        [(t("nav.warnings"), WarningSettings(action="policy", chat=chat).pack())],
        [(t("nav.protection"), cb("protection", chat))],
        [(t("nav.settings"), LocalAction(action="settings", chat=chat).pack())],
        [(t("nav.history"), cb("history", chat))],
        [(t("nav.to_groups"), cb())],
    ]
    await display(
        message,
        t("gh.title") + "\n\n<b>" + escape(group.title) + "</b>\n\n" + t("gh.choose"),
        keyboard(rows),
        edit=True,
    )


async def ads_panel(message, service, actor, chat, page):
    await access(service, message.bot, chat, actor, configure=True)
    await AdsService(service.settings, service.session).authorize(actor)
    entries = list(
        await service.session.scalars(
            select(AdCampaign)
            .join(AdSchedule)
            .where(
                AdSchedule.chat_id == chat,
                AdSchedule.excluded.is_(False),
                AdCampaign.status == "DRAFT",
            )
            .order_by(AdCampaign.id.desc())
            .offset(page * 4)
            .limit(5)
        )
    )
    rows = [
        [(ad.internal_name[:60], AutoAd(action="panel", ad=ad.id).pack())] for ad in entries[:4]
    ]
    pages = []
    if page:
        pages.append(("◀", cb("ads", chat, page - 1)))
    if len(entries) > 4:
        pages.append(("▶", cb("ads", chat, page + 1)))
    if pages:
        rows.append(pages)
    rows += [
        [(t("gh.all_ads"), AutoAd(action="root").pack())],
        [(t("button.back"), cb("group", chat))],
    ]
    await display(
        message,
        t("gh.title")
        + "\n\n<b>AUTO ADS</b>\n\n"
        + t("gh.ads_shared")
        + ("\n\n" + t("ua.empty") if not entries else ""),
        keyboard(rows),
        edit=True,
    )


async def root(message, service, actor):
    rows = [
        [(t("nav.groups"), cb())],
        [(t("nav.moderation_history"), cb("history_groups"))],
        [(t("nav.commands"), cb("help"))],
        [
            (
                t("button.back_admin") if await service.is_admin(actor) else t("button.home"),
                action("admin" if await service.is_admin(actor) else "home"),
            )
        ],
    ]
    await display(message, t("gh.title") + "\n\n" + t("gh.choose"), keyboard(rows), edit=True)


async def history(message, service, actor, chat, page):
    await access(service, message.bot, chat, actor)
    total = (
        await service.session.scalar(
            select(func.count()).select_from(GroupSanction).where(GroupSanction.chat_id == chat)
        )
        or 0
    )
    page = min(page, max(0, (total - 1) // 5))
    entries = list(
        await service.session.scalars(
            select(GroupSanction)
            .where(GroupSanction.chat_id == chat)
            .order_by(GroupSanction.id.desc())
            .offset(page * 5)
            .limit(5)
        )
    )
    text = (
        t("nav.history")
        + "\n\n"
        + (
            "\n\n".join(
                t(
                    "nav.sanction_row",
                    id=r.id,
                    kind=escape(r.kind),
                    status=escape(r.status),
                    user=r.telegram_id,
                    admin=r.actor_id,
                    time=f"{r.created_at:%Y-%m-%d %H:%M}",
                    reason=escape(r.reason[:200]) or "—",
                )
                for r in entries
            )
            or t("nav.empty")
        )
    )
    rows = [
        [
            ("◀", cb("history", chat, max(0, page - 1))),
            (f"{page + 1}/{max(1, (total + 4) // 5)}", action("noop")),
            ("▶", cb("history", chat, min(max(0, (total - 1) // 5), page + 1))),
        ],
        [(t("lm.history"), LocalAction(action="action", chat=chat, value="HISTORY").pack())],
        [(t("lm.warnings"), LocalAction(action="action", chat=chat, value="WARNINGS").pack())],
        [(t("button.back"), cb("group", chat))],
    ]
    await display(message, text, keyboard(rows), edit=True)


async def protection(message, service, actor, chat):
    await access(service, message.bot, chat, actor, configure=True)
    rows = [
        [(t("checks.title"), CheckSettings(action="root", chat=chat).pack())],
        [(t("bw.title"), CheckSettings(action="words", chat=chat).pack())],
        [(t("button.back"), cb("group", chat))],
    ]
    await display(message, t("nav.protection"), keyboard(rows), edit=True)


def register_group_help(router: Router):
    @router.callback_query(GroupHelp.filter())
    async def handle(callback: CallbackQuery, callback_data: GroupHelp, state: FSMContext, service):
        try:
            m, actor, d = callback.message, callback.from_user.id, callback_data
            if (
                not isinstance(m, Message)
                or m.chat.type != "private"
                or m.chat.id != actor
                or not 0 <= d.page <= 10000
            ):
                raise DomainError("group_moderation_forbidden")
            await clear_flow(state)
            if d.action == "root":
                await root(m, service, actor)
            elif d.action in {"groups", "history_groups"}:
                await groups(m, service, actor, d.page, history=d.action == "history_groups")
            elif d.action == "help":
                await display(
                    m,
                    t("nav.commands") + "\n\n" + t("nav.group_help"),
                    keyboard([[(t("button.back"), cb("root"))]]),
                    edit=True,
                )
            elif d.action == "history":
                await history(m, service, actor, d.chat, d.page)
            elif d.action == "protection":
                await protection(m, service, actor, d.chat)
            elif d.action == "group":
                await panel(m, service, actor, d.chat)
            elif d.action == "ads":
                await ads_panel(m, service, actor, d.chat, d.page)
                await state.update_data(gh_ads_chat=d.chat)
            else:
                raise DomainError("checks_invalid")
            await callback.answer()
        except (DomainError, TelegramAPIError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await callback.answer(
                t("checks." + error.code)
                if isinstance(error, DomainError) and error.code.startswith("checks_")
                else t("lm.permission"),
                show_alert=True,
            )

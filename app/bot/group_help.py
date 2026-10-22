"""Group-first private settings; every entry rechecks Telegram group authority."""

import asyncio
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.ads_services import AdsService
from app.bot.callbacks import AutoAd, CheckSettings, GroupHelp, WarningSettings
from app.bot.group_keyboards import GroupAction
from app.bot.keyboards import action, keyboard
from app.bot.local_admin import access, display
from app.bot.screens import clear_flow
from app.errors import DomainError
from app.group_authorization import GroupAuthorizer
from app.group_services import GroupService
from app.i18n import t
from app.models import AdCampaign, AdSchedule, GroupModerationSettings, ManagedGroup


def cb(name="groups", chat=0, page=0):
    return GroupHelp(action=name, chat=chat, page=page).pack()


async def groups(message, service, actor, page=0):
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
    rows = [[(g.title[:55], cb("group", g.chat_id))] for g in allowed[page * 5 : page * 5 + 5]]
    pages = []
    if page:
        pages.append(("◀", cb(page=page - 1)))
    if len(allowed) > page * 5 + 5:
        pages.append(("▶", cb(page=page + 1)))
    if pages:
        rows.append(pages)
    if service.access.is_owner(actor):
        rows.append([(t("button.groups"), GroupAction(action="list").pack())])
    rows.append([(t("button.back"), action("admin" if await service.is_admin(actor) else "home"))])
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
        [
            (t("ws.button"), WarningSettings(action="policy", chat=chat).pack()),
            (t("checks.title"), CheckSettings(action="root", chat=chat).pack()),
        ],
        [(t("bw.title"), CheckSettings(action="words", chat=chat).pack())],
    ]
    if await service.is_admin(actor):
        rows.append([(t("ads.title"), cb("ads", chat))])
    rows.append([(t("gh.change"), cb())])
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
            if d.action == "groups":
                await groups(m, service, actor, d.page)
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

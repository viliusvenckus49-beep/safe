"""Private per-group warning policy; all transitions edit the same Telegram panel."""

import asyncio
from html import escape

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select

from app.bot.callbacks import WarningSettings
from app.bot.keyboards import action, keyboard
from app.bot.local_admin import access, display
from app.bot.moderation_messages import duration_text, identity_fields, identity_labels
from app.bot.screens import clear_flow
from app.bot.warning_panel import details
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t
from app.models import GroupModerationSettings, GroupSanction, ManagedGroup
from app.moderation_services import GroupModerationService, module_enabled
from app.services import Service
from app.warning_services import active_warnings, configure_warnings

DURATIONS = (300, 600, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800, 2592000)


def cb(name: str, chat: int = 0, value: str = "", version: int = 0) -> str:
    return WarningSettings(action=name, chat=chat, value=value, version=version).pack()


async def groups(message: Message, service: Service, actor: int, page: int = 0) -> None:
    entries = list(
        await service.session.scalars(
            select(ManagedGroup)
            .where(
                ManagedGroup.approved.is_(True),
                ManagedGroup.enabled.is_(True),
                ~GroupService.removed_group(ManagedGroup.chat_id),
            )
            .order_by(ManagedGroup.chat_id)
        )
    )
    semaphore = asyncio.Semaphore(4)

    async def permitted(group):
        async with semaphore:
            try:
                await GroupModerationService(service.session, message.bot).authorization.actor(
                    group.chat_id, actor, configure=True
                )
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
    rows = [[(g.title[:55], cb("policy", g.chat_id))] for g in allowed[page * 5 : page * 5 + 5]]
    pages = []
    if page:
        pages.append(("◀", cb("groups", value=str(page - 1))))
    if len(allowed) > page * 5 + 5:
        pages.append(("▶", cb("groups", value=str(page + 1))))
    if pages:
        rows.append(pages)
    rows.append([(t("ws.back"), action("group_help" if await service.is_admin(actor) else "home"))])
    await display(
        message,
        t("ws.button") + "\n\n" + t("lm.groups" if allowed else "lm.empty"),
        keyboard(rows),
        edit=True,
    )


async def policy_panel(message: Message, policy: GroupModerationSettings | None, chat: int) -> None:
    limit = policy.warning_limit if policy else 3
    selected = policy.warning_action if policy else "MUTE"
    version = policy.warning_version if policy else 0

    def choice(kind):
        return (
            t("ws." + kind),
            cb("action", chat, kind, version),
        )

    rows = [
        [(t("ws.warned"), cb("warned", chat))],
        [choice("OFF"), choice("KICK")],
        [choice("MUTE"), choice("BAN")],
        [(t("ws.duration"), cb("duration", chat))],
        [
            (str(n) + (" ✅" if n == limit else ""), cb("limit", chat, str(n), version))
            for n in range(2, 7)
        ],
        [(t("ws.back"), cb("groups"))],
    ]
    await display(
        message,
        t("ws.menu", punishment=t("ws.name." + selected), limit=limit),
        keyboard(rows),
        edit=True,
    )


async def warned(message: Message, service: Service, chat: int, page: int) -> None:
    counts = (
        select(GroupSanction.telegram_id, func.count().label("warning_count"))
        .where(
            GroupSanction.chat_id == chat,
            GroupSanction.kind == "WARN",
            GroupSanction.status == "RECORDED",
        )
        .group_by(GroupSanction.telegram_id)
        .order_by(GroupSanction.telegram_id)
    )
    entries = (await service.session.execute(counts.offset(page * 5).limit(6))).all()
    labels = await identity_labels(
        service.session, {r.telegram_id for r in entries[:5]}, bot=message.bot, chat=chat
    )
    rows = [
        [(f"{labels[r.telegram_id]} · {r.warning_count}", cb("member", chat, str(r.telegram_id)))]
        for r in entries[:5]
    ]
    pages = []
    if page:
        pages.append(("◀", cb("warned", chat, str(page - 1))))
    if len(entries) > 5:
        pages.append(("▶", cb("warned", chat, str(page + 1))))
    if pages:
        rows.append(pages)
    rows.append([(t("ws.back"), cb("policy", chat))])
    await display(
        message,
        "<b>" + t("ws.warned") + "</b>\n\n" + t("ws.choose_member" if entries else "lm.no_history"),
        keyboard(rows),
        edit=True,
    )


async def member(message: Message, service: Service, chat: int, value: str) -> None:
    parts = value.split(".")
    target, page = int(parts[0]), int(parts[1]) if len(parts) == 2 else 0
    if len(parts) > 2 or target <= 0 or page < 0 or page > 100000:
        raise DomainError("local_invalid")
    entries = list(
        await service.session.scalars(
            active_warnings(chat, target)
            .order_by(GroupSanction.id.desc())
            .offset(page * 5)
            .limit(6)
        )
    )
    labels = await identity_labels(
        service.session, {target, *(r.actor_id for r in entries[:5])}, bot=message.bot, chat=chat
    )
    text = "<b>" + t("ws.warned") + "</b>\n\n" + identity_fields(target, labels[target])
    text += "\n\n" + (
        "\n\n".join(details(r, labels[r.actor_id]) for r in entries[:5])
        if entries
        else t("lm.no_history")
    )
    rows = []
    pages = []
    if page:
        pages.append(("◀", cb("member", chat, f"{target}.{page - 1}")))
    if len(entries) > 5:
        pages.append(("▶", cb("member", chat, f"{target}.{page + 1}")))
    if pages:
        rows.append(pages)
    rows.append([(t("ws.back"), cb("warned", chat))])
    await display(message, text, keyboard(rows), edit=True)


def register_warning_settings_handlers(router: Router) -> None:
    @router.callback_query(WarningSettings.filter())
    async def handle(
        callback: CallbackQuery, callback_data: WarningSettings, state: FSMContext, service: Service
    ):
        try:
            message, actor, d = callback.message, callback.from_user.id, callback_data
            if (
                not isinstance(message, Message)
                or message.chat.type != "private"
                or message.chat.id != actor
            ):
                raise DomainError("group_moderation_forbidden")
            if d.action == "groups":
                if not d.value.isdecimal() and d.value:
                    raise DomainError("local_invalid")
                page = int(d.value or "0")
                if not 0 <= page <= 100000:
                    raise DomainError("local_invalid")
                await clear_flow(state)
                await groups(message, service, actor, page)
            else:
                local = await access(service, callback.bot, d.chat, actor, configure=True)
                policy = await service.session.get(
                    GroupModerationSettings, d.chat, populate_existing=True
                )
                if d.action in {"action", "limit", "mute"}:
                    if d.action == "limit" and d.value not in {str(n) for n in range(2, 7)}:
                        raise DomainError("local_invalid")
                    if d.action == "action" and d.value not in {"OFF", "KICK", "MUTE", "BAN"}:
                        raise DomainError("local_invalid")
                    seconds = (
                        int(d.value)
                        if d.action == "mute"
                        else (policy.warning_mute_seconds if policy else 86400)
                    )
                    if d.action == "mute" and (
                        seconds not in DURATIONS
                        or (policy.warning_action if policy else "MUTE") != "MUTE"
                    ):
                        raise DomainError("local_invalid")
                    policy = await configure_warnings(
                        local,
                        d.chat,
                        actor,
                        expected_version=d.version,
                        limit=int(d.value)
                        if d.action == "limit"
                        else (policy.warning_limit if policy else 3),
                        action=d.value
                        if d.action == "action"
                        else (policy.warning_action if policy else "MUTE"),
                        mute_seconds=seconds,
                    )
                    await policy_panel(message, policy, d.chat)
                    if policy.warning_action != "OFF" and (
                        not module_enabled(policy, "WARN")
                        or not module_enabled(policy, policy.warning_action)
                    ):
                        await callback.answer(
                            t("ws.modules", punishment=t("ws.name." + policy.warning_action)),
                            show_alert=True,
                        )
                        return
                elif d.action == "policy":
                    await clear_flow(state)
                    await policy_panel(message, policy, d.chat)
                elif d.action == "duration":
                    if policy and policy.warning_action != "MUTE":
                        await callback.answer(t("ws.choose_mute"), show_alert=True)
                        return
                    version = policy.warning_version if policy else 0
                    chosen = policy.warning_mute_seconds if policy else 86400
                    rows = [
                        [
                            (
                                duration_text(n, "MUTE") + (" ✅" if chosen == n else ""),
                                cb("mute", d.chat, str(n), version),
                            )
                            for n in DURATIONS[i : i + 2]
                        ]
                        for i in range(0, len(DURATIONS), 2)
                    ]
                    rows.append([(t("ws.back"), cb("policy", d.chat))])
                    await display(
                        message,
                        "<b>"
                        + t("ws.duration")
                        + "</b>\n\n"
                        + t("ws.current_duration", duration=escape(duration_text(chosen, "MUTE"))),
                        keyboard(rows),
                        edit=True,
                    )
                elif d.action == "warned":
                    page = int(d.value or "0")
                    if not 0 <= page <= 100000:
                        raise DomainError("local_invalid")
                    await warned(message, service, d.chat, page)
                elif d.action == "member":
                    await member(message, service, d.chat, d.value)
                else:
                    raise DomainError("local_invalid")
            await callback.answer()
        except (
            DomainError,
            TelegramAPIError,
            ValueError,
            OverflowError,
            TimeoutError,
            OSError,
        ) as error:
            await service.session.rollback()
            from app.bot.local_admin import error_text

            key = (
                {
                    "group_moderation_request_conflict": "ws.stale",
                    "group_moderation_invalid_duration": "ws.duration_unavailable",
                }.get(error.code)
                if isinstance(error, DomainError)
                else None
            )
            await callback.answer(t(key) if key else error_text(error), show_alert=True)

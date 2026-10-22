"""Screenshot-style inline expansion; private, live-authorized, per-group policy."""

from html import escape
from typing import Any

from aiogram import Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from app.bot.callbacks import CheckSettings, GroupHelp
from app.bot.keyboards import action
from app.bot.local_admin import access, display, error_text
from app.bot.moderation_messages import duration_text
from app.bot.screens import clear_flow
from app.bot.states import CheckSettingsFlow
from app.checks import ACTIONS, NAME_BLOCKS, OBLIGATIONS, RULES, configure
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t
from app.models import GroupCheckEvent, GroupCheckSettings, GroupSanction, ManagedGroup

DURATIONS = (300, 600, 1800, 3600, 7200, 21600, 43200, 86400, 172800, 604800, 2592000)


def cb(name, chat=0, value="", version=0):
    return CheckSettings(action=name, chat=chat, value=value, version=version).pack()


def button(text, data, selected=False, *, action_choice=False):
    return InlineKeyboardButton(
        text=("» " + text + " «") if selected and not action_choice else text,
        callback_data=data,
        style=("success" if action_choice else "primary") if selected else None,
    )


def back(chat, tab="root"):
    return [button(t("checks.back"), cb(tab, chat))]


async def words_panel(message, policy, chat):
    version = policy.version if policy else 0
    selected = policy.words_action if policy else "OFF"
    delete = policy.words_delete if policy else True
    rows = []
    for kinds in (("OFF", "WARN", "KICK"), ("MUTE", "BAN")):
        rows.append(
            [
                button(
                    t("checks." + kind),
                    cb("w_action", chat, kind, version),
                    selected == kind,
                    action_choice=True,
                )
                for kind in kinds
            ]
        )
    rows += [
        [
            button(
                t("bw.delete") + (" ✓" if delete else " ✗"), cb("w_delete", chat, version=version)
            )
        ],
        [
            button(t("bw.add"), cb("w_add", chat, version=version)),
            button(t("bw.remove"), cb("w_remove", chat, version=version)),
        ],
        [button(t("bw.list"), cb("w_list", chat))],
    ]
    if selected == "MUTE":
        rows.append([button(t("checks.duration"), cb("w_duration", chat))])
    rows.append([button(t("checks.back"), GroupHelp(action="group", chat=chat).pack())])
    await display(
        message,
        t(
            "bw.intro",
            action=t("checks." + selected),
            delete="✓" if delete else "✗",
            count=len(policy.banned_words) if policy else 0,
        ),
        InlineKeyboardMarkup(inline_keyboard=rows),
        edit=True,
    )


async def groups(message, service, actor, page=0):
    allowed = []
    for group in await service.session.scalars(
        select(ManagedGroup)
        .where(
            ManagedGroup.enabled.is_(True),
            ManagedGroup.approved.is_(True),
            ~GroupService.removed_group(ManagedGroup.chat_id),
        )
        .order_by(ManagedGroup.chat_id)
    ):
        try:
            await access(service, message.bot, group.chat_id, actor, configure=True)
            allowed.append(group)
        except (DomainError, TelegramAPIError, TimeoutError, OSError):
            continue
    page = min(page, max(0, (len(allowed) - 1) // 5))
    rows = [[button(g.title[:55], cb("root", g.chat_id))] for g in allowed[page * 5 : page * 5 + 5]]
    pages = []
    if page:
        pages.append(button("◀", cb("groups", value=str(page - 1))))
    if len(allowed) > page * 5 + 5:
        pages.append(button("▶", cb("groups", value=str(page + 1))))
    if pages:
        rows.append(pages)
    rows.append(
        [
            button(
                t("checks.back"), action("group_help" if await service.is_admin(actor) else "home")
            )
        ]
    )
    await display(
        message,
        "<b>" + t("checks.title") + "</b>\n\n" + t("lm.groups" if allowed else "lm.empty"),
        InlineKeyboardMarkup(inline_keyboard=rows),
        edit=True,
    )


async def panel(message, policy, chat, tab="root", expanded=""):
    actions = policy.actions if policy else {}
    version = policy.version if policy else 0
    text = t(
        "checks.intro",
        obligations="\n".join(
            "• " + escape(t("checks." + r)) + ": " + escape(t("checks." + actions.get(r, "OFF")))
            for r in OBLIGATIONS
        ),
        blocks="\n".join(
            "• " + escape(t("checks." + r)) + ": " + escape(t("checks." + actions.get(r, "OFF")))
            for r in NAME_BLOCKS
        ),
    )
    rows = []
    if tab == "root":
        text += "\n\n" + t("checks.help")
    rows.append(
        [
            button(t("checks.obligations"), cb("obligations", chat), tab == "obligations"),
            button(t("checks.blocks"), cb("blocks", chat), tab == "blocks"),
        ]
    )
    if tab in {"obligations", "blocks"}:
        for rule in OBLIGATIONS if tab == "obligations" else NAME_BLOCKS:
            rows.append([button(t("checks." + rule), cb("rule", chat, rule), expanded == rule)])
            if expanded == rule:
                for offset in (0, 3):
                    rows.append(
                        [
                            button(
                                t("checks." + kind),
                                cb("set", chat, rule + "." + kind, version),
                                actions.get(rule, "OFF") == kind,
                                action_choice=True,
                            )
                            for kind in ACTIONS[offset : offset + 3]
                        ]
                    )
                if rule == "spam":
                    rows.append([button(t("checks.terms"), cb("terms", chat, version=version))])
                if actions.get(rule) == "MUTE":
                    rows.append([button(t("checks.duration"), cb("duration", chat))])
    else:
        for field, label, default in [
            ("check_on_join", "join", True),
            ("delete_messages", "delete", False),
            ("notify_result", "result", True),
        ]:
            value = getattr(policy, field) if policy else default
            rows.append(
                [
                    button(
                        t("checks." + label) + (" ✓" if value else " ✗"),
                        cb("toggle", chat, field, version),
                    )
                ]
            )
        rows.append([button(t("checks.history"), cb("history", chat))])
    rows.append(
        back(chat)
        if tab != "root"
        else [button(t("checks.back"), GroupHelp(action="group", chat=chat).pack())]
    )
    await display(message, text, InlineKeyboardMarkup(inline_keyboard=rows), edit=True)


def register_checks_handlers(router: Router):
    @router.callback_query(CheckSettings.filter())
    async def handle(
        callback: CallbackQuery, callback_data: CheckSettings, state: FSMContext, service
    ):
        d = callback_data
        try:
            message = callback.message
            if (
                not isinstance(message, Message)
                or message.chat.type != "private"
                or message.chat.id != callback.from_user.id
            ):
                raise DomainError("group_moderation_forbidden")
            actor = callback.from_user.id
            if d.action == "groups":
                page = int(d.value or "0")
                if not 0 <= page <= 100000:
                    raise DomainError("checks_invalid")
                await clear_flow(state)
                await groups(message, service, actor, page)
            else:
                local = await access(service, callback.bot, d.chat, actor, configure=True)
                await local.ensure_commands(d.chat, actor)
                policy = await service.session.get(
                    GroupCheckSettings, d.chat, populate_existing=True
                )
                changes: dict[str, Any]
                tab, expanded = "root", ""
                if d.action == "words" or d.action.startswith("w_"):
                    await clear_flow(state)
                    if d.action in {"w_action", "w_delete", "w_mute"}:
                        changes = (
                            {"words_action": d.value}
                            if d.action == "w_action"
                            else {"words_delete": not (policy.words_delete if policy else True)}
                            if d.action == "w_delete"
                            else {"mute_seconds": int(d.value)}
                        )
                        if d.action == "w_mute" and int(d.value) not in DURATIONS:
                            raise DomainError("checks_invalid")
                        policy = await configure(
                            service.session, callback.bot, d.chat, actor, d.version, **changes
                        )
                    elif d.action in {"w_add", "w_remove", "w_help"}:
                        await state.set_state(CheckSettingsFlow.terms)
                        await state.set_data(
                            {
                                "chat": d.chat,
                                "version": d.version,
                                "panel": message.message_id,
                                "words": "w_add" if d.action == "w_help" else d.action,
                            }
                        )
                        rows = []
                        if d.action == "w_add":
                            rows.append(
                                [button(t("bw.more"), cb("w_help", d.chat, version=d.version))]
                            )
                        if d.action == "w_help":
                            rows.append(
                                [button(t("checks.back"), cb("w_add", d.chat, version=d.version))]
                            )
                        rows.append([button(t("bw.cancel"), cb("words", d.chat))])
                        await display(
                            message,
                            t(
                                "bw.help"
                                if d.action == "w_help"
                                else "bw.add_prompt"
                                if d.action == "w_add"
                                else "bw.remove_prompt"
                            ),
                            InlineKeyboardMarkup(inline_keyboard=rows),
                            edit=True,
                        )
                        await callback.answer()
                        return
                    elif d.action == "w_list":
                        values = policy.banned_words if policy else []
                        start = int(d.value or "0")
                        if not 0 <= start <= 100:
                            raise DomainError("checks_invalid")
                        rows = []
                        pages = []
                        if start:
                            pages.append(button("◀", cb("w_list", d.chat, str(max(0, start - 20)))))
                        if len(values) > start + 20:
                            pages.append(button("▶", cb("w_list", d.chat, str(start + 20))))
                        if pages:
                            rows.append(pages)
                        rows.append(back(d.chat, "words"))
                        await display(
                            message,
                            "<b>"
                            + t("bw.list")
                            + "</b>\n\n"
                            + ("\n".join(escape(v) for v in values[start : start + 20]) or "—"),
                            InlineKeyboardMarkup(inline_keyboard=rows),
                            edit=True,
                        )
                        await callback.answer()
                        return
                    elif d.action == "w_duration":
                        rows = [
                            [
                                button(
                                    duration_text(n, "MUTE"),
                                    cb("w_mute", d.chat, str(n), policy.version if policy else 0),
                                )
                                for n in DURATIONS[i : i + 2]
                            ]
                            for i in range(0, len(DURATIONS), 2)
                        ]
                        await display(
                            message,
                            "<b>" + t("checks.duration") + "</b>",
                            InlineKeyboardMarkup(inline_keyboard=[*rows, back(d.chat, "words")]),
                            edit=True,
                        )
                        await callback.answer()
                        return
                    elif d.action != "words":
                        raise DomainError("checks_invalid")
                    await words_panel(message, policy, d.chat)
                    await callback.answer()
                    return
                if d.action in {"root", "obligations", "blocks"}:
                    await clear_flow(state)
                    tab = d.action
                elif d.action == "rule":
                    if d.value not in RULES:
                        raise DomainError("checks_invalid")
                    expanded = d.value
                    tab = "obligations" if expanded in OBLIGATIONS else "blocks"
                elif d.action in {"set", "toggle", "mute"}:
                    changes = {}
                    if d.action == "set":
                        rule, kind = d.value.split(".")
                        if rule not in RULES or kind not in ACTIONS:
                            raise DomainError("checks_invalid")
                        changes = {"actions": {**(policy.actions if policy else {}), rule: kind}}
                        tab, expanded = ("obligations" if rule in OBLIGATIONS else "blocks"), rule
                    elif d.action == "toggle":
                        if d.value not in {"check_on_join", "delete_messages", "notify_result"}:
                            raise DomainError("checks_invalid")
                        changes = {
                            d.value: not (
                                getattr(policy, d.value) if policy else d.value != "delete_messages"
                            )
                        }
                    else:
                        seconds = int(d.value)
                        if seconds not in DURATIONS:
                            raise DomainError("checks_invalid")
                        changes = {"mute_seconds": seconds}
                    policy = await configure(
                        service.session, callback.bot, d.chat, actor, d.version, **changes
                    )
                elif d.action == "duration":
                    rows = [
                        [
                            button(
                                duration_text(n, "MUTE")
                                + (" ✓" if policy and n == policy.mute_seconds else ""),
                                cb("mute", d.chat, str(n), policy.version if policy else 0),
                            )
                            for n in DURATIONS[i : i + 2]
                        ]
                        for i in range(0, len(DURATIONS), 2)
                    ]
                    await display(
                        message,
                        "<b>" + t("checks.duration") + "</b>",
                        InlineKeyboardMarkup(inline_keyboard=[*rows, back(d.chat)]),
                        edit=True,
                    )
                    await callback.answer()
                    return
                elif d.action == "terms":
                    await clear_flow(state)
                    await state.set_state(CheckSettingsFlow.terms)
                    await state.set_data(
                        {"chat": d.chat, "version": d.version, "panel": message.message_id}
                    )
                    await display(
                        message,
                        t("checks.terms_prompt"),
                        InlineKeyboardMarkup(inline_keyboard=[back(d.chat, "blocks")]),
                        edit=True,
                    )
                    await callback.answer()
                    return
                elif d.action == "history":
                    events = list(
                        await service.session.scalars(
                            select(GroupCheckEvent)
                            .where(GroupCheckEvent.chat_id == d.chat)
                            .order_by(GroupCheckEvent.id.desc())
                            .limit(8)
                        )
                    )
                    lines = []
                    for event in events:
                        sanction = (
                            await service.session.get(GroupSanction, event.sanction_id)
                            if event.sanction_id
                            else None
                        )
                        lines.append(
                            f"<b>#{event.id}</b> · <code>{event.telegram_id}</code> · {escape(t('checks.' + event.rule))}\n{event.created_at:%Y-%m-%d %H:%M UTC} · {escape(event.action)} · {escape(sanction.status if sanction else event.state)}\n{escape(event.result or '—')}"
                        )
                    await display(
                        message,
                        "<b>"
                        + t("checks.history")
                        + "</b>\n\n"
                        + ("\n\n".join(lines) or t("checks.empty")),
                        InlineKeyboardMarkup(inline_keyboard=[back(d.chat)]),
                        edit=True,
                    )
                    await callback.answer()
                    return
                else:
                    raise DomainError("checks_invalid")
                await panel(message, policy, d.chat, tab, expanded)
            await callback.answer()
        except (
            DomainError,
            TelegramAPIError,
            TimeoutError,
            OSError,
            ValueError,
            OverflowError,
        ) as error:
            await service.session.rollback()
            text = (
                t("checks." + error.code)
                if isinstance(error, DomainError) and error.code.startswith("checks_")
                else error_text(error)
            )
            await callback.answer(text, show_alert=True)

    @router.message(CheckSettingsFlow.terms)
    async def terms(message: Message, state: FSMContext, service):
        if message.chat.type != "private" or not message.from_user:
            return
        data = await state.get_data()
        try:
            await access(service, message.bot, data["chat"], message.from_user.id, configure=True)
            content = (message.text or "").strip()
            if not content or content.startswith("/"):
                raise DomainError("checks_invalid")
            values = (
                []
                if content == "-"
                else list(
                    dict.fromkeys(line.strip() for line in content.splitlines() if line.strip())
                )
            )
            changes = {"spam_terms": values}
            if data.get("words"):
                old = await service.session.get(
                    GroupCheckSettings, data["chat"], populate_existing=True
                )
                current = old.banned_words if old else []
                from app.checks import normalized

                changes = {
                    "banned_words": list(dict.fromkeys([*current, *values]))
                    if data["words"] == "w_add"
                    else [
                        v for v in current if normalized(v) not in {normalized(s) for s in values}
                    ]
                }
            policy = await configure(
                service.session,
                message.bot,
                data["chat"],
                message.from_user.id,
                data["version"],
                **changes,
            )
            # Same private panel; received text is never treated as a callback or HTML.
            original = Message(message_id=data["panel"], date=message.date, chat=message.chat).as_(
                message.bot
            )
            if data.get("words"):
                await words_panel(original, policy, data["chat"])
            else:
                await panel(original, policy, data["chat"], "blocks", "spam")
            await clear_flow(state)
        except (DomainError, TelegramAPIError, TimeoutError, OSError) as error:
            await service.session.rollback()
            await message.answer(
                t("checks." + error.code)
                if isinstance(error, DomainError) and error.code.startswith("checks_")
                else error_text(error)
            )

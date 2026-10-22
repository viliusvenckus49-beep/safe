"""Recipient-only WARN management, reusing durable reset confirmations and history."""

from html import escape
from typing import Any

from aiogram.types import Message
from sqlalchemy import select

from app.bot.keyboards import keyboard
from app.bot.moderation_messages import identity_fields, identity_labels
from app.errors import DomainError
from app.i18n import t
from app.models import GroupModerationSettings, GroupSanction
from app.moderation_services import GroupModerationService, utc
from app.warning_services import active_warnings, reset_prompt


async def panel(
    local: GroupModerationService,
    message: Message,
    actor: int,
    origin: GroupSanction,
    *,
    action: str = "manage",
    value: str = "",
    edit: bool = False,
) -> None:
    from app.bot.moderation_controls import control

    if origin.kind != "WARN":
        raise DomainError("group_moderation_stale_control")
    if message.chat.id not in {origin.chat_id, actor}:
        raise DomainError("group_moderation_stale_control")
    await local._check_group(origin.chat_id)
    await local.authorization.actor(origin.chat_id, actor)
    await local.authorization.bot_rights(origin.chat_id)
    # Read-only history remains available even if the former member is now an admin.
    rows = list(await local.session.scalars(active_warnings(origin.chat_id, origin.telegram_id)))
    labels = await identity_labels(
        local.session, {origin.telegram_id}, bot=message.bot, chat=origin.chat_id
    )
    policy = await local.session.get(GroupModerationSettings, origin.chat_id)
    text = (
        "<b><u>CRIMSON • WARN CONTROL</u></b>\n\n"
        + identity_fields(origin.telegram_id, labels[origin.telegram_id])
        + "\n"
        + t("lm.control.active_count", count=len(rows), limit=policy.warning_limit if policy else 3)
    )
    back = [(t("lm.back"), control("manage", origin.id))]
    buttons: list[list[tuple[str, str]]] = []
    if action == "manage":
        if rows:
            buttons += [
                [(t("lm.control.remove_one"), control("list", origin.id))],
                [(t("lm.control.remove_all"), control("all", origin.id))],
            ]
        buttons += [[(t("lm.control.history"), control("history", origin.id))]]
        buttons += [[(t("lm.back"), control("close", origin.id))]]
    elif action in {"list", "history"}:
        offset = int(value or 0)
        if not 0 <= offset <= 1_000_000:
            raise DomainError("local_invalid")
        query = select(GroupSanction).where(
            GroupSanction.chat_id == origin.chat_id,
            GroupSanction.telegram_id == origin.telegram_id,
            GroupSanction.kind == "WARN",
        )
        if action == "list":
            query = query.where(GroupSanction.status == "RECORDED")
        entries = list(
            await local.session.scalars(
                query.order_by(GroupSanction.id.desc()).offset(offset).limit(6)
            )
        )
        if not entries:
            text += "\n\n" + t("lm.no_history")
        for entry in entries[:5]:
            people = await identity_labels(
                local.session, {entry.actor_id}, bot=message.bot, chat=origin.chat_id
            )
            text += "\n\n" + details(entry, people[entry.actor_id])
            if action == "list":
                buttons += [
                    [
                        (
                            t("lm.control.select", id=entry.id),
                            control("one", origin.id, str(entry.id)),
                        )
                    ]
                ]
        if offset:
            buttons += [[(t("lm.back"), control(action, origin.id, str(max(0, offset - 5))))]]
        if len(entries) > 5:
            buttons += [[(t("lm.next"), control(action, origin.id, str(offset + 5)))]]
        buttons += [back]
    elif action in {"one", "all"}:
        chosen = (
            next((row for row in rows if str(row.id) == value), None) if action == "one" else None
        )
        if action == "one" and chosen is None:
            raise DomainError("group_moderation_stale_control")
        if not rows:
            raise DomainError("group_moderation_stale_control")
        # The all-warning confirmation also works from a receipt whose original
        # warning has since been cleared; bind it to a currently active warning.
        prompt = await reset_prompt(
            local, chosen or rows[-1], actor, selected_warning_id=chosen.id if chosen else None
        )
        if chosen is not None:
            people = await identity_labels(
                local.session, {chosen.actor_id}, bot=message.bot, chat=origin.chat_id
            )
            text += "\n\n" + details(chosen, people[chosen.actor_id])
        text += "\n\n" + t(
            "lm.control.single_confirm" if chosen else "lm.control.reset_confirm",
            count=prompt.expected_count,
        )
        buttons = [
            [
                (t("lm.control.yes"), control("yes", prompt.origin_id, prompt.id)),
                (t("lm.control.no"), control("no", prompt.origin_id, prompt.id)),
            ],
            [(t("lm.back"), control("manage", origin.id, prompt.id))],
        ]
    else:
        raise DomainError("group_moderation_stale_control")
    text += "\n\n🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™"
    from app.bot.private_panels import show

    await show(message, actor, text, keyboard(buttons), edit=edit)


def details(row: Any, administrator: str) -> str:
    return (
        f"<b>WARN #{row.id}</b> · {escape(t('lm.state.' + row.status))}"
        f"\n{utc(row.created_at).strftime('%Y-%m-%d %H:%M UTC')}"
        + "\n"
        + t("lm.reason", reason=escape((row.reason or t("lm.control.no_reason"))[:300]))
        + f"\n👑 <b>{t('lm.notice.admin')}:</b> {escape(administrator)} · <code>{row.actor_id}</code>"
    )

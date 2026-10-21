"""Read-only formatting of confirmed local moderation receipts; no enforcement here."""

import asyncio
from html import escape
from math import ceil
from typing import Any

from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import language, t
from app.models import (
    AuditEvent,
    GroupModerationDraft,
    GroupSanction,
    GroupSanctionJob,
    GroupWarningEscalation,
    User,
)
from app.moderation_services import utc

BRAND = "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™"
HEADINGS = {
    "BAN": "⛔️ 𝗨𝗦𝗘𝗥 • 𝗕𝗔𝗡𝗡𝗘𝗗",
    "WARN": "⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗",
    "MUTE": "🔇 𝗨𝗦𝗘𝗥 • 𝗠𝗨𝗧𝗘𝗗",
    "KICK": "🚪 𝗨𝗦𝗘𝗥 • 𝗞𝗜𝗖𝗞𝗘𝗗",
    "UNBAN": "🔓 𝗨𝗦𝗘𝗥 • 𝗨𝗡𝗕𝗔𝗡𝗡𝗘𝗗",
    "UNMUTE": "🔊 𝗨𝗦𝗘𝗥 • 𝗨𝗡𝗠𝗨𝗧𝗘𝗗",
    "UNWARN": "✅ 𝗨𝗦𝗘𝗥 • 𝗨𝗡𝗪𝗔𝗥𝗡𝗘𝗗",
}


def compact(value: str) -> str:
    """User text cannot add headings/blank fields to the receipt layout."""
    return " ".join(value.split())


def identity_label(user: User | TelegramUser | None, telegram_id: int) -> str:
    actual_id = user.telegram_id if isinstance(user, User) else user.id if user else None
    if user is not None and actual_id == telegram_id:
        if user.username and compact(user.username).lstrip("@"):
            return "@" + compact(user.username).lstrip("@")
        name = compact(user.display_name if isinstance(user, User) else user.full_name)
        if name and name not in {"Vartotojas", "User", f"User {telegram_id}"}:
            return name
    return str(telegram_id)


async def observed_users(
    ids: set[int],
    *,
    observed: tuple[TelegramUser, ...] = (),
    bot: Any = None,
    chat: int | None = None,
) -> tuple[TelegramUser, ...]:
    """Take a read-only display snapshot, including before a target leaves the group."""
    seen = {person.id: person for person in observed if person.id in ids}
    getter = getattr(bot, "get_chat_member", None)
    if callable(getter) and chat is not None:
        for user_id in sorted(ids):
            try:
                member = await asyncio.wait_for(getter(chat, user_id), timeout=2)
                user = member.user
                if type(user.id) is not int or user.id != user_id:
                    continue
                if isinstance(user, TelegramUser):
                    seen[user_id] = user
                elif getattr(user, "username", None) or getattr(user, "first_name", None):
                    seen[user_id] = TelegramUser(
                        id=user_id,
                        is_bot=bool(getattr(user, "is_bot", False)),
                        first_name=getattr(user, "first_name", "") or "",
                        last_name=getattr(user, "last_name", None),
                        username=getattr(user, "username", None),
                    )
            except TelegramRetryAfter:
                break  # No further identity reads inside a Telegram FloodWait.
            except (TelegramAPIError, TimeoutError, OSError, ValueError):
                continue  # Formatting failure must never retry or fail a sanction.
    return tuple(seen.values())


async def identity_labels(
    session: AsyncSession,
    ids: set[int],
    *,
    observed: tuple[TelegramUser, ...] = (),
    bot: Any = None,
    chat: int | None = None,
) -> dict[int, str]:
    """Read names by pinned numeric ID; a display lookup never changes an action."""
    seen = {
        person.id: person
        for person in await observed_users(ids, observed=observed, bot=bot, chat=chat)
    }
    people = {
        person.telegram_id: person
        for person in await session.scalars(select(User).where(User.telegram_id.in_(ids)))
    }
    return {
        user_id: identity_label(seen.get(user_id) or people.get(user_id), user_id)
        for user_id in ids
    }


def identity_fields(telegram_id: int, label: str) -> str:
    fields = []
    if compact(label) != str(telegram_id):
        fields.append(f"👤 <b>{t('lm.notice.user')}:</b> {escape(compact(label))}")
    fields.append(f"🆔 <b>{t('lm.notice.id')}:</b> <code>{telegram_id}</code>")
    return "\n".join(fields)


def duration_text(seconds: int | None, kind: str) -> str:
    if seconds is None:
        return t("lm.notice.permanent")
    # Draft creation is just after parsing; round to minutes without reducing 7d
    # to 6d 23h 59m. Never use current time: the issued duration must remain stable.
    minutes = max(1, ceil(seconds / 60))
    if minutes % 1440 == 0 and not (kind == "MUTE" and minutes == 1440):
        count, unit = minutes // 1440, "day"
    elif minutes % 60 == 0:
        count, unit = minutes // 60, "hour"
    else:
        count, unit = minutes, "minute"
    lang = language.get()
    if lang == "en":
        form = "one" if count == 1 else "other"
    elif lang == "ru":
        form = (
            "one"
            if count % 10 == 1 and count % 100 != 11
            else "few"
            if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14
            else "other"
        )
    else:
        form = (
            "one"
            if count % 10 == 1 and not 11 <= count % 100 <= 19
            else "few"
            if 2 <= count % 10 <= 9 and not 11 <= count % 100 <= 19
            else "other"
        )
    return f"{count} {t('lm.notice.' + unit + '.' + form)}"


def format_success(
    kind: str,
    telegram_id: int,
    *,
    user: str,
    admin: str,
    reason: str = "",
    duration_seconds: int | None = None,
    warnings: int | None = None,
) -> str:
    """Labels use real HTML bold; every external value is escaped exactly once."""
    fields = ["<b>" + t("lm.control.warn_title") + "</b>" if kind == "WARN" else HEADINGS[kind]]
    if compact(user) != str(telegram_id):
        fields.append(f"👤 <b>{t('lm.notice.user')}:</b> {escape(compact(user))}")
    fields.append(f"🆔 <b>{t('lm.notice.id')}:</b> <code>{telegram_id}</code>")
    if kind in {"BAN", "MUTE"}:
        fields.append(
            f"⏳ <b>{t('lm.notice.duration')}:</b> {escape(duration_text(duration_seconds, kind))}"
        )
    if kind == "WARN" and warnings is not None:
        fields.append(f"⚠️ <b>{t('lm.notice.warnings')}:</b> {warnings}/3")
    if compact(reason):
        fields.append(f"📋 <b>{t('lm.notice.reason')}:</b> {escape(compact(reason))}")
    fields += [f"👑 <b>{t('lm.notice.admin')}:</b> {escape(compact(admin))}", BRAND]
    return "\n\n".join(fields)


async def success_message(
    session: AsyncSession,
    draft: GroupModerationDraft,
    sanction: GroupSanction,
    *,
    observed: tuple[TelegramUser, ...] = (),
    bot: Any = None,
) -> str:
    # Render the completed reversal in the original public receipt, using the
    # administrator who actually reversed it, not the original sanction issuer.
    reversed_kind, reversed_actor = None, None
    if draft.kind in {"BAN", "MUTE"} and sanction.status == "REVOKED":
        release = await session.scalar(
            select(GroupSanctionJob)
            .where(
                GroupSanctionJob.sanction_id == sanction.id,
                GroupSanctionJob.operation == "RELEASE",
                GroupSanctionJob.status == "SUCCEEDED",
                GroupSanctionJob.result == "RELEASED",
                GroupSanctionJob.actor_id.is_not(None),
            )
            .execution_options(populate_existing=True)
        )
        if release is not None:
            reversed_kind = "UNBAN" if draft.kind == "BAN" else "UNMUTE"
            reversed_actor = release.actor_id
    elif draft.kind == "WARN" and sanction.status == "CANCELLED":
        events = await session.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == "group_moderation_warnings_cancelled",
                AuditEvent.details["chat_id"].as_integer() == sanction.chat_id,
                AuditEvent.details["user_id"].as_integer() == sanction.telegram_id,
            )
            .order_by(AuditEvent.id.desc())
        )
        event = next(
            (event for event in events if sanction.id in event.details.get("warning_ids", [])), None
        )
        if event is not None:
            reversed_kind, reversed_actor = "UNWARN", event.actor_id
    if reversed_kind is not None and reversed_actor is not None:
        labels = await identity_labels(
            session,
            {draft.telegram_id, reversed_actor},
            observed=observed,
            bot=bot,
            chat=draft.chat_id,
        )
        return format_success(
            reversed_kind,
            draft.telegram_id,
            user=labels[draft.telegram_id],
            admin=labels[reversed_actor],
        )
    labels = await identity_labels(
        session, {draft.telegram_id, draft.actor_id}, observed=observed, bot=bot, chat=draft.chat_id
    )
    warnings = None
    if draft.kind == "WARN":
        # Stable count at this warning, isolated to this group and numeric user.
        # Only active warnings count; cleared and consumed history is retained.
        warnings = await session.scalar(
            select(func.count())
            .select_from(GroupSanction)
            .where(
                GroupSanction.chat_id == draft.chat_id,
                GroupSanction.telegram_id == draft.telegram_id,
                GroupSanction.kind == "WARN",
                GroupSanction.status == "RECORDED",
                GroupSanction.id <= sanction.id,
            )
        )
    seconds = (
        max(0, round((utc(sanction.until_date) - utc(draft.created_at)).total_seconds()))
        if sanction.until_date is not None
        else None
    )
    text = format_success(
        draft.kind,
        draft.telegram_id,
        user=labels[draft.telegram_id],
        admin=labels[draft.actor_id],
        reason=draft.reason,
        duration_seconds=seconds,
        warnings=warnings,
    )

    if draft.kind == "WARN" and sanction.status in {"CANCELLED", "COMPLETED"}:
        text += "\n\n" + t(
            "lm.control.warn_cancelled"
            if sanction.status == "CANCELLED"
            else "lm.control.warn_consumed"
        )
    if draft.kind == "WARN" and (warnings or 0) >= 3:
        event = await session.scalar(
            select(GroupWarningEscalation)
            .where(
                GroupWarningEscalation.chat_id == sanction.chat_id,
                GroupWarningEscalation.telegram_id == sanction.telegram_id,
            )
            .order_by(GroupWarningEscalation.id.desc())
            .limit(1)
        )
        if event is not None:
            linked = (
                await session.get(GroupSanction, event.sanction_id) if event.sanction_id else None
            )
            state = linked.status if linked is not None else event.state
            text += "\n\n" + t(
                "lm.control.auto_"
                + (
                    "uncertain"
                    if state == "UNCERTAIN"
                    else "pending"
                    if state == "PENDING"
                    else "failed"
                )
            )
    if draft.kind in {"BAN", "MUTE"} and sanction.status in {"REVOKED", "EXPIRED"}:
        text += "\n\n" + t(
            "lm.control.released" if sanction.status == "REVOKED" else "lm.control.expired"
        )
    if draft.kind == "MUTE" and sanction.status == "COMPLETED":
        replacement = await session.scalar(
            select(GroupSanction)
            .where(
                GroupSanction.replaces_sanction_id == sanction.id,
                GroupSanction.owned.is_(True),
            )
            .order_by(GroupSanction.id.desc())
            .limit(1)
        )
        if replacement is not None:
            text += "\n\n" + t("lm.control.replaced", kind=replacement.kind)
    automatic = (
        await session.scalar(
            select(GroupWarningEscalation.id).where(
                GroupWarningEscalation.sanction_id == sanction.id
            )
        )
        if draft.kind == "MUTE"
        else None
    )
    if automatic is not None:
        text += "\n\n" + t("lm.control.auto_reason")
    return text

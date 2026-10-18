"""Read-only formatting of confirmed local moderation receipts; no enforcement here."""

from html import escape
from math import ceil

from aiogram.types import User as TelegramUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n import language, t
from app.models import GroupModerationDraft, GroupSanction, User
from app.moderation_services import utc

BRAND = "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™"
HEADINGS = {
    "BAN": "⛔️ 𝗨𝗦𝗘𝗥 • 𝗕𝗔𝗡𝗡𝗘𝗗",
    "WARN": "⚠️ 𝗨𝗦𝗘𝗥 • 𝗪𝗔𝗥𝗡𝗘𝗗",
    "MUTE": "🔇 𝗨𝗦𝗘𝗥 • 𝗠𝗨𝗧𝗘𝗗",
    "KICK": "🚪 𝗨𝗦𝗘𝗥 • 𝗞𝗜𝗖𝗞𝗘𝗗",
    "UNBAN": "🔓 𝗨𝗦𝗘𝗥 • 𝗨𝗡𝗕𝗔𝗡𝗡𝗘𝗗",
    "UNMUTE": "🔊 𝗨𝗦𝗘𝗥 • 𝗨𝗡𝗠𝗨𝗧𝗘𝗗",
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
    fields = [
        HEADINGS[kind],
        f"👤 <b>{t('lm.notice.user')}:</b> {escape(compact(user))}",
        f"🆔 <b>{t('lm.notice.id')}:</b> <code>{telegram_id}</code>",
    ]
    if kind in {"BAN", "MUTE"}:
        fields.append(
            f"⏳ <b>{t('lm.notice.duration')}:</b> {escape(duration_text(duration_seconds, kind))}"
        )
    if kind == "WARN" and warnings is not None:
        fields.append(f"⚠️ <b>{t('lm.notice.warnings')}:</b> {warnings}")
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
) -> str:
    # Numeric identity lookup only: never resolve/reassign a username for an action.
    people = {
        person.telegram_id: person
        for person in await session.scalars(
            select(User).where(User.telegram_id.in_({draft.telegram_id, draft.actor_id}))
        )
    }
    seen = {person.id: person for person in observed}
    warnings = None
    if draft.kind == "WARN":
        # Stable count at this warning, isolated to this group and numeric user.
        # Stage 2 has no configured warning limit, so never invent a denominator.
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
    return format_success(
        draft.kind,
        draft.telegram_id,
        user=identity_label(
            seen.get(draft.telegram_id) or people.get(draft.telegram_id), draft.telegram_id
        ),
        admin=identity_label(
            seen.get(draft.actor_id) or people.get(draft.actor_id), draft.actor_id
        ),
        reason=draft.reason,
        duration_seconds=seconds,
        warnings=warnings,
    )

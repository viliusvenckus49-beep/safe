"""Activity profiles reuse the existing identity and username history services."""

import asyncio
from datetime import UTC, datetime
from html import escape
from typing import Any

import structlog
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.bot import keyboards as kb
from app.bot.validation import message_content
from app.group_services import GroupService
from app.i18n import t
from app.models import ManagedGroup, ProfileActivity, UsernameHistory

BRAND = "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™"
THRESHOLDS = (
    (0, 0),
    (5, 3),
    (25, 7),
    (75, 14),
    (150, 30),
    (350, 60),
    (750, 90),
    (1500, 180),
    (3000, 270),
    (6000, 365),
)


def progression(messages: int, days: int) -> tuple[int, int]:
    level = max(i for i, (m, d) in enumerate(THRESHOLDS) if messages >= m and days >= d)
    if level == 9:
        return level, 100
    m, d = THRESHOLDS[level]
    next_m, next_d = THRESHOLDS[level + 1]
    percent = min((messages - m) * 100 // (next_m - m), (days - d) * 100 // (next_d - d))
    return level, min(100, max(0, percent))


async def active_groups(service: Any) -> list[ManagedGroup]:
    return list(
        await service.repo.session.scalars(
            select(ManagedGroup).where(
                ManagedGroup.approved.is_(True),
                ManagedGroup.enabled.is_(True),
                ~GroupService.removed_group(ManagedGroup.chat_id),
            )
        )
    )


async def record_activity(message: Message, service: Any) -> None:
    actor = message.from_user
    if not actor or actor.is_bot or message.sender_chat:
        return
    content = message_content(message).lstrip()
    if any(
        entity.type == "bot_command"
        for entity in (message.entities or message.caption_entities or [])
    ):
        return
    if content.startswith("/") or content.lower().startswith(("+rep", "-rep")):
        return
    if not any(
        getattr(message, field, None)
        for field in (
            "text",
            "photo",
            "video",
            "audio",
            "voice",
            "document",
            "sticker",
            "animation",
            "video_note",
            "contact",
            "location",
            "venue",
            "poll",
            "dice",
        )
    ):
        return
    group = await service.repo.session.get(ManagedGroup, message.chat.id)
    if group is None or not group.approved or not group.enabled:
        return
    if await GroupService(service.settings, service.repo.session).is_removed_group(message.chat.id):
        return
    user = await service.repo.user_by_telegram(actor.id)
    if user is None:
        user = await service.observe(actor.id, actor.username, actor.full_name)
    insert = pg_insert if service.repo.session.bind.dialect.name == "postgresql" else sqlite_insert
    await service.repo.session.execute(
        insert(ProfileActivity)
        .values(
            chat_id=message.chat.id,
            message_id=message.message_id,
            user_id=user.id,
            active_date=message.date.astimezone(UTC).date(),
        )
        .on_conflict_do_nothing(index_elements=["chat_id", "message_id"])
    )


async def profile_data(service: Any, bot: Any, target: str) -> dict[str, Any]:
    user = await service.resolve(target, refresh_identity=True)
    session = service.repo.session
    row = (
        await session.execute(
            select(
                func.count(),
                func.count(distinct(ProfileActivity.active_date)),
                func.min(ProfileActivity.active_date),
            ).where(ProfileActivity.user_id == user.id)
        )
    ).one()
    groups = await active_groups(service)
    # Release identity locks before contacting Telegram. Membership is never inferred from backups.
    await session.commit()

    async def member(group: ManagedGroup) -> bool:
        if not user.telegram_id:
            return False
        try:
            result = await asyncio.wait_for(bot.get_chat_member(group.chat_id, user.telegram_id), 3)
            return result.status in {"member", "administrator", "creator"} or (
                result.status == "restricted" and bool(getattr(result, "is_member", False))
            )
        except (TelegramAPIError, TimeoutError) as error:
            structlog.get_logger().info(
                "profile_membership_unconfirmed",
                user_id=user.telegram_id,
                chat_id=group.chat_id,
                exception_type=type(error).__name__,
            )
            return False

    count = sum(await asyncio.gather(*(member(group) for group in groups)))
    moderator = (
        user.telegram_id is not None
        and not service.access.is_owner(user.telegram_id)
        and await service.access.is_admin(user.telegram_id)
    )
    return {
        "user": user,
        "moderator": moderator,
        "messages": row[0],
        "days": row[1],
        "groups": count,
        "network_days": max(0, (datetime.now(UTC).date() - row[2]).days) if row[2] else 0,
    }


def profile_text(data: dict[str, Any]) -> str:
    user = data["user"]
    name = "@" + user.username if user.username else user.display_name
    level, percent = progression(data["messages"], data["days"])
    levels = t("redsafe.levels").split("|")
    stats = t("redsafe.stats", **data)
    status = t("redsafe.moderator") if data.get("moderator") else levels[level]
    return (
        f"{BRAND}\n\n🪪 <b>{t('redsafe.title')}</b>\n━━━━━━━━━━━━━━\n\n"
        f"👤 {escape(name)}\n🏷 {level + 1:02d} · <b>{status}</b>\n"
        f"🆔 ID: {user.telegram_id or 'UNKNOWN'}\n\n<blockquote><i>{stats}</i></blockquote>\n\n"
        f"━━━━━━━━━━━━━━\n◈ <b>{t('redsafe.progress')}</b>\n\n"
        f"🏅 {t('redsafe.next')}: <b>{levels[min(level + 1, 9)]}</b>\n\n"
        f"{'▰' * (percent // 10)}{'▱' * (10 - percent // 10)} {percent}%"
    )


def controls(user_id: int):
    return kb.keyboard(
        [
            [(t("redsafe.names_button"), kb.action("redsafe_names", f"u:{user_id}"))],
        ]
    )


async def names_text(service: Any, target: str) -> str:
    user = await service.resolve(target)
    rows = list(
        await service.repo.session.scalars(
            select(UsernameHistory)
            .where(UsernameHistory.user_id == user.id)
            .order_by(UsernameHistory.observed_at.desc(), UsernameHistory.id.desc())
            .limit(30)
        )
    )
    history = "\n".join(f"@{escape(row.username)} · {row.observed_at:%Y-%m-%d}" for row in rows)
    return f"{BRAND}\n\n<b>{t('redsafe.names_title')}</b>\n━━━━━━━━━━━━━━\n\n" + (
        history or t("redsafe.names_empty")
    )

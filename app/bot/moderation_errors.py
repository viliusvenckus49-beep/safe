"""Private, command-bound diagnostics; a lookup failure is not proof of absence."""

import re
from html import escape
from typing import Any

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.methods import GetChatMember
from aiogram.types import (
    EphemeralMessageParameters,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.errors import DomainError
from app.i18n import t

COMMANDS = {"warn", "unwarn", "warns", "ban", "mute", "kick", "unban", "unmute"}


def command_name(value: str) -> str:
    token = value.split(maxsplit=1)[0] if value.strip() else ""
    match = re.fullmatch(r"/?([A-Za-z]+)(?:@([A-Za-z][A-Za-z0-9_]{4,31}))?", token)
    if not match or match[1].lower() not in COMMANDS:
        return "/moderation"
    return "/" + match[1].lower() + ("@" + match[2] if match[2] else "")


def basic_kind(error: Exception) -> str | None:
    if isinstance(error, DomainError):
        if error.code in {
            "group_moderation_invalid_target",
            "group_moderation_invalid_id",
            "group_moderation_invalid_username",
            "group_moderation_target_not_found",
        }:
            return "target_missing"
        if error.code in {"group_moderation_username_unknown", "group_moderation_username_changed"}:
            return "identity_unverified"
        if error.code == "group_moderation_forbidden":
            return "actor_rights"
        if error.code == "group_moderation_bot_rights":
            return "bot_rights"
        if error.code in {
            "group_moderation_identity_mismatch",
            "group_moderation_group_unavailable",
        }:
            return "check_unavailable"
        if error.code == "local_invalid":
            return "input_invalid"
        return {
            "group_moderation_target_not_member": "not_in_group",
            "group_moderation_invalid_duration": "duration_invalid",
            "group_moderation_protected_target": "protected_target",
            "group_moderation_scam_protected": "scam",
            "group_moderation_busy": "busy",
            "group_moderation_uncertain": "uncertain",
            "group_moderation_invalid_draft": "stale",
            "group_moderation_stale_control": "stale",
            "group_moderation_restriction_changed": "changed",
            "group_moderation_request_conflict": "stale",
            "group_moderation_no_warnings": "no_warnings",
            "group_moderation_supergroup_required": "supergroup",
        }.get(error.code)
    return None


async def diagnose_state(local: Any, chat: int, actor: int, target: int, command: str) -> str:
    """Only fresh verified Telegram reads justify an already/clear explanation."""
    from sqlalchemy import select

    from app.models import GroupSanction, now
    from app.moderation_services import utc

    try:
        await local._check_group(chat)
        await local.authorization.actor(chat, actor)
        await local.authorization.bot_rights(chat)
        member = await local.authorization.member(chat, target)
        requested = command_name(command).split("@")[0]
        from sqlalchemy import or_

        from app.models import GroupSanctionJob

        outstanding = list(
            await local.session.scalars(
                select(GroupSanctionJob.status)
                .join(GroupSanction, GroupSanction.id == GroupSanctionJob.sanction_id)
                .where(
                    GroupSanction.chat_id == chat,
                    GroupSanction.telegram_id == target,
                    GroupSanctionJob.status.in_({"PENDING", "PROCESSING", "UNCERTAIN"}),
                    or_(
                        GroupSanctionJob.operation == "APPLY",
                        GroupSanctionJob.status.in_({"PROCESSING", "UNCERTAIN"}),
                        GroupSanctionJob.available_at <= now(),
                    ),
                )
            )
        )
        if "UNCERTAIN" in outstanding:
            return "uncertain"
        if outstanding:
            return "busy"
        if requested in {"/mute", "/ban", "/kick"}:
            if member.status == "kicked":
                return "already_banned"
            if (
                requested == "/mute"
                and member.status == "restricted"
                and getattr(member, "can_send_messages", None) is False
            ):
                return "already_muted"
            if member.status == "restricted":
                return "restriction"
            return "check_unavailable"
        if requested in {"/unmute", "/unban"}:
            if requested == "/unmute" and member.status == "kicked":
                return "already_banned"
            if requested == "/unban" and member.status == "restricted":
                return "already_unbanned"
            expected = "restricted" if requested == "/unmute" else "kicked"
            if member.status == expected:
                return "foreign"
            # Another kind of restriction is not proof of an unrestricted member.
            if member.status not in {"member", "left", "administrator", "creator"}:
                return "check_unavailable"
            latest = await local.session.scalar(
                select(GroupSanction)
                .where(
                    GroupSanction.chat_id == chat,
                    GroupSanction.telegram_id == target,
                    GroupSanction.kind.in_(["MUTE"] if requested == "/unmute" else ["BAN", "KICK"]),
                )
                .order_by(GroupSanction.id.desc())
                .limit(1)
            )
            if (
                latest
                and latest.status == "EXPIRED"
                and latest.until_date
                and utc(latest.until_date) <= now()
            ):
                return "expired"
            return "already_unmuted" if requested == "/unmute" else "already_unbanned"
    except (DomainError, TelegramAPIError, TimeoutError, OSError) as error:
        return basic_kind(error) or "check_unavailable"
    return "check_unavailable"


def target_read(error: Exception, chat: int, actor: int, bot_id: int) -> int | None:
    """Only an actual target getChatMember failure may trigger absence diagnosis."""
    method = getattr(error, "method", None)
    if (
        isinstance(error, TelegramBadRequest)
        and isinstance(method, GetChatMember)
        and method.chat_id == chat
        and type(method.user_id) is int
        and 0 < method.user_id < 2**63
        and method.user_id not in {actor, bot_id}
    ):
        return method.user_id
    return None


async def diagnose_target(
    local: Any,
    chat: int,
    actor: int,
    target: int,
    *,
    username: str | None = None,
) -> str:
    """Read after a failure, never retry a sanction or reject a valid departed target."""
    try:
        await local._check_group(chat)  # Short SQL check commits before network calls.
        await local.authorization.actor(chat, actor)
        await local.authorization.bot_rights(chat)
    except (DomainError, TelegramAPIError, TimeoutError, OSError) as error:
        return basic_kind(error) or "check_unavailable"
    try:
        member = await local.authorization.member(chat, target)
    except TelegramBadRequest as error:
        description = error.message.upper()
        if not username and any(
            code in description for code in ("USER_ID_INVALID", "PEER_ID_INVALID", "USER_NOT_FOUND")
        ):
            return "target_missing"
        return "identity_unverified" if username else "check_unavailable"
    except (DomainError, TelegramAPIError, TimeoutError, OSError):
        return "identity_unverified" if username else "check_unavailable"
    if username:
        current = getattr(member.user, "username", None)
        if not isinstance(current, str) or current.casefold() != username.lstrip("@").casefold():
            return "identity_unverified"
    # member() has already verified the returned numeric ID. Unknown status is
    # never interpreted as absence; restricted users may still be actual members.
    if member.status in {"left", "kicked"} or (
        member.status == "restricted" and getattr(member, "is_member", None) is False
    ):
        return "not_in_group"
    return "identity_unverified" if username else "check_unavailable"


def error_message(
    command: str,
    kind: str | None,
    *,
    fallback: str,
) -> str:
    command = escape(command_name(command))
    header = t("lm.diagnostic." + (kind or "general") + ".title")
    body = t("lm.diagnostic." + kind + ".body", command=command) if kind else escape(fallback)
    result = "<b><u>" + escape(header) + "</u></b>\n\n" + body
    if kind == "target_missing":
        return result
    return result + "\n\n" + t("lm.diagnostic.command", command=command)


async def deliver_error(
    message: Message,
    actor: int | None,
    text: str,
    command: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
    allow_public_hint: bool = True,
) -> Message | None:
    """Use native recipient-only group messages; never publish diagnostic details."""
    bot = message.bot
    if bot is None:
        raise RuntimeError("Message has no bot context")
    if message.chat.type == "private" and message.chat.id == actor and message.chat.id > 0:
        return (
            await message.answer(text, parse_mode="HTML", reply_markup=reply_markup)
            if actor is not None
            else None
        )
    if type(actor) is int and 0 < actor < 2**63:
        try:
            return await bot.send_message(
                message.chat.id,
                text,
                parse_mode="HTML",
                message_thread_id=message.message_thread_id,
                ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=actor),
                reply_markup=reply_markup,
            )
        except TelegramRetryAfter:
            return None
        except (TelegramBadRequest, TelegramForbiddenError):
            # Explicit rejection permits a private fallback. Never remove the
            # ephemeral parameter and resend diagnostic text to the group.
            pass
        except (TelegramAPIError, TimeoutError, OSError):
            # Unknown delivery outcome: do not duplicate the diagnostic via DM.
            return None
        try:
            return await bot.send_message(actor, text, parse_mode="HTML", reply_markup=reply_markup)
        except TelegramRetryAfter:
            return None
        except (TelegramBadRequest, TelegramForbiddenError):
            pass
        except (TelegramAPIError, TimeoutError, OSError):
            return None
    if not allow_public_hint:
        return None
    # Both private delivery methods were rejected, or no actor was verifiable.
    # Only a generic navigation hint may be public; it contains no target data.
    markup = None
    try:
        me = await bot.me()
        if isinstance(me.username, str) and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{4,31}", me.username
        ):
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("lm.open_private_button"), url=f"https://t.me/{me.username}"
                        )
                    ]
                ]
            )
    except TelegramRetryAfter:
        return None
    except (TelegramAPIError, TimeoutError, OSError):
        pass
    try:
        await message.answer(
            t("lm.diagnostic.open_private", command=escape(command_name(command))),
            parse_mode="HTML",
            reply_markup=markup,
        )
    except (TelegramAPIError, TimeoutError, OSError):
        pass
    return None

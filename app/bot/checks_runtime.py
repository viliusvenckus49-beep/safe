"""Durable 15-second security notices feed the existing sanction executor."""

import asyncio
from datetime import timedelta
from html import escape

import structlog
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from sqlalchemy import and_, or_, select, update

from app.checks import API_TIMEOUT, evidence, is_member, observe, policy_for_event
from app.errors import DomainError
from app.group_authorization import GroupAuthorizer
from app.i18n import t, use_language
from app.models import GroupCheckEvent, GroupCheckSettings, GroupSanction, GroupSanctionJob, now
from app.moderation_services import GroupModerationService, audit, lock_group, utc

GRACE_SECONDS = 15
ACTION_NAMES = {
    "ADVISE": "ADVISE",
    "WARN": "WARNED",
    "KICK": "KICKED",
    "MUTE": "MUTED",
    "BAN": "BANNED",
}


def security_text(event, user=None, *, planned=False):
    name = escape(getattr(user, "full_name", None) or event.display_name or str(event.telegram_id))
    label = t("checks.planned" if planned else "checks.action")
    action = ACTION_NAMES[event.action]
    text = (
        "🛡 <b>REDSAFE SECURITY</b>"
        f"\n\n👤 <b>{name}</b> [<code>{event.telegram_id}</code>]"
        f"\n\n⚠️ <b>{t('checks.reason_label')}:</b> {escape(t('checks.reason.' + event.rule))}"
        f"\n🚫 <b>{label}:</b> <b>{action}</b>"
    )
    if planned:
        text += "\n" + t("checks.delay")
    return text + "\n\n<i>" + escape(t("checks.hint." + event.rule)) + "</i>"


async def inspect_message(message, sessions):
    if (
        message.bot is None
        or message.sender_chat
        or not message.from_user
        or message.from_user.is_bot
    ):
        return
    if message.new_chat_members or message.left_chat_member:
        return
    await observe(
        sessions, message.bot, message.chat.id, message.from_user, message_id=message.message_id
    )


async def inspect_join(event, sessions):
    if (
        event.bot is None
        or is_member(event.old_chat_member)
        or not is_member(event.new_chat_member)
    ):
        return
    await observe(
        sessions,
        event.bot,
        event.chat.id,
        event.new_chat_member.user,
        joined_at=event.date,
        check_join=True,
    )


async def _edit_notice(bot, event, text):
    if event.notice_message_id:
        try:
            await asyncio.wait_for(
                bot.edit_message_text(
                    text,
                    chat_id=event.chat_id,
                    message_id=event.notice_message_id,
                    parse_mode="HTML",
                ),
                API_TIMEOUT,
            )
            return True
        except (TelegramAPIError, TimeoutError, OSError):
            pass
    return False


async def _finish_notice(session, bot, event, policy):
    sanction = await session.get(GroupSanction, event.sanction_id)
    release_failed = (
        sanction is not None
        and sanction.kind == "KICK"
        and await session.scalar(
            select(GroupSanctionJob.id)
            .where(
                GroupSanctionJob.sanction_id == sanction.id,
                GroupSanctionJob.operation == "RELEASE",
                GroupSanctionJob.status.in_({"FAILED", "CANCELLED", "UNCERTAIN"}),
            )
            .limit(1)
        )
    )
    if sanction is not None and (sanction.status == "UNCERTAIN" or release_failed):
        event.state, event.result = "UNCERTAIN", "NATIVE_OUTCOME_UNCONFIRMED"
        await session.commit()
        if policy.notify_result:
            await _edit_notice(bot, event, t("checks.uncertain"))
        return
    if sanction is None or sanction.status in {"CANCELLED", "FAILED", "UNCERTAIN"}:
        event.state, event.result = "FAILED", sanction.status if sanction else "NO_SANCTION"
        await session.commit()
        if policy.notify_result:
            await _edit_notice(bot, event, t("checks.cancelled"))
        return
    success = (
        sanction.status in {"RECORDED", "COMPLETED"}
        if sanction.kind == "WARN"
        else sanction.status == ("COMPLETED" if sanction.kind == "KICK" else "ACTIVE")
    )
    if sanction.status in {"REVOKED", "EXPIRED"}:
        event.state, event.result = "DONE", "ALREADY_RELEASED"
        await session.commit()
        if policy.notify_result:
            await _edit_notice(bot, event, t("checks.released"))
        return
    if not success:
        event.available_at = now() + timedelta(seconds=5)
        await session.commit()
        return
    event.state, event.result = "DONE", "CONFIRMED"
    await session.commit()
    if policy.notify_result:
        done = await _edit_notice(bot, event, security_text(event))
        event.result = "CONFIRMED_NOTICE_UPDATED" if done else "CONFIRMED_NOTICE_UPDATE_FAILED"
        await session.commit()


async def _process(sessions, bot, identifier):
    async with sessions() as session:
        event = await session.get(GroupCheckEvent, identifier)
        if event is None:
            return
        await lock_group(session, event.chat_id)
        await session.refresh(event)
        policy = await session.get(GroupCheckSettings, event.chat_id, populate_existing=True)
        if policy is None:
            return
        with use_language(policy.language):
            if event.state in {"RESOLVED", "CANCELLED"}:
                if event.notice_message_id and event.result in {
                    "NOW_COMPLIANT",
                    "SETTINGS_CHANGED",
                }:
                    event.result += ":NOTICE_CLOSED"
                    await session.commit()
                    if policy.notify_result:
                        await _edit_notice(bot, event, t("checks.cancelled"))
                return
            if event.state == "QUEUED":
                await _finish_notice(session, bot, event, policy)
                return
            if event.state not in {"PENDING", "WAITING"} or utc(event.available_at) > now():
                return
            try:
                policy = await policy_for_event(session, event)
                await session.commit()
                member = await GroupAuthorizer(bot).member(event.chat_id, event.telegram_id)
                if (
                    not is_member(member)
                    or member.status in {"administrator", "creator"}
                    or member.user.is_bot
                ):
                    raise DomainError("checks_exempt_or_left")
                bad, unknown = await evidence(
                    session, bot, policy, member.user, member, only=event.rule
                )
                if event.rule not in bad or unknown:
                    raise DomainError("checks_unconfirmed")
                await GroupAuthorizer(bot).actor(event.chat_id, policy.updated_by)
                rights = await GroupAuthorizer(bot).member(event.chat_id, bot.id)
                if rights.status != "administrator" or (
                    event.action == "DELETE"
                    and getattr(rights, "can_delete_messages", False) is not True
                ):
                    raise DomainError("group_moderation_bot_rights")
                if event.state == "WAITING":
                    if event.execute_after is not None and utc(event.execute_after) > now():
                        event.available_at = event.execute_after
                        await session.commit()
                        return
                    await session.commit()
                    await GroupModerationService(session, bot).submit(
                        event.chat_id,
                        policy.updated_by,
                        event.telegram_id,
                        event.action,
                        until_date=(
                            utc(event.execute_after) + timedelta(seconds=policy.mute_seconds)
                        ).replace(microsecond=0)
                        if event.action == "MUTE"
                        else None,
                        reason=t("checks.reason." + event.rule),
                        key="checks:" + str(event.id),
                        check_event_id=event.id,
                    )
                    return
                await lock_group(session, event.chat_id)
                await session.refresh(event)
                policy = await policy_for_event(session, event)
                if event.state != "PENDING":
                    return
                event.state, event.updated_at = "SENDING", now()
                event.attempts += 1
                await session.commit()  # Persist IO fence; no sleep or SQL lock while waiting.
                try:
                    if event.action == "DELETE":
                        result = await asyncio.wait_for(
                            bot.delete_message(event.chat_id, event.message_id), API_TIMEOUT
                        )
                        event.state, event.result = (
                            ("DONE", "CONFIRMED") if result is True else ("FAILED", "API_FALSE")
                        )
                    else:
                        result = await asyncio.wait_for(
                            bot.send_message(
                                event.chat_id,
                                security_text(event, member.user, planned=event.action != "ADVISE"),
                                parse_mode="HTML",
                            ),
                            API_TIMEOUT,
                        )
                        if not getattr(result, "message_id", None):
                            event.state, event.result = "FAILED", "NOTICE_NOT_CONFIRMED"
                        else:
                            event.notice_message_id = result.message_id
                            event.notice_sent_at = now()
                            event.execute_after = event.notice_sent_at + timedelta(
                                seconds=GRACE_SECONDS
                            )
                            event.state = "DONE" if event.action == "ADVISE" else "WAITING"
                            event.available_at = event.execute_after
                            event.result = (
                                "ADVISED" if event.action == "ADVISE" else "NOTICE_DELIVERED"
                            )
                except TelegramRetryAfter as error:
                    # Telegram explicitly rejected this delivery; a bounded retry is safe.
                    event.state = "PENDING" if event.attempts < 5 else "FAILED"
                    event.available_at = now() + timedelta(seconds=max(1, error.retry_after))
                    event.result = "TelegramRetryAfter"
                except (TelegramAPIError, TimeoutError, OSError):
                    event.state, event.result = "UNCERTAIN", "DELIVERY_UNCONFIRMED"
                audit(
                    session,
                    "check_delivery",
                    policy.updated_by,
                    event.chat_id,
                    event_id=event.id,
                    rule=event.rule,
                    operation=event.action,
                    result=event.result,
                    user_id=event.telegram_id,
                )
                await session.commit()
            except (DomainError, TelegramAPIError, TimeoutError, OSError) as error:
                await session.rollback()
                event = await session.get(GroupCheckEvent, identifier, populate_existing=True)
                if event is None or event.state not in {"PENDING", "WAITING"}:
                    return
                policy = await session.get(
                    GroupCheckSettings, event.chat_id, populate_existing=True
                )
                if policy is None:
                    event.state, event.result = "CANCELLED", "SETTINGS_MISSING"
                    await session.commit()
                    return
                event.attempts += 1
                event.result = (
                    error.code if isinstance(error, DomainError) else type(error).__name__
                )
                if isinstance(error, DomainError):
                    event.state = "SKIPPED"
                elif event.attempts >= 5:
                    event.state = "FAILED"
                event.available_at = now() + timedelta(
                    seconds=max(
                        30, error.retry_after if isinstance(error, TelegramRetryAfter) else 60
                    )
                )
                audit(
                    session,
                    "check_skipped",
                    policy.updated_by,
                    event.chat_id,
                    event_id=event.id,
                    result=event.result,
                    rule=event.rule,
                    user_id=event.telegram_id,
                )
                await session.commit()
                if (
                    event.notice_message_id
                    and policy.notify_result
                    and event.state in {"SKIPPED", "FAILED"}
                ):
                    await _edit_notice(bot, event, t("checks.cancelled"))


async def process_checks(sessions, bot, limit=10):
    async with sessions() as session:
        await session.execute(
            update(GroupCheckEvent)
            .where(
                GroupCheckEvent.state == "SENDING",
                GroupCheckEvent.updated_at < now() - timedelta(seconds=30),
            )
            .values(state="UNCERTAIN", result="DELIVERY_INTERRUPTED")
        )
        await session.commit()
        ready = or_(
            GroupCheckEvent.state.in_({"PENDING", "WAITING", "QUEUED"}),
            and_(
                GroupCheckEvent.state.in_({"RESOLVED", "CANCELLED"}),
                GroupCheckEvent.notice_message_id.is_not(None),
                GroupCheckEvent.result.in_({"NOW_COMPLIANT", "SETTINGS_CHANGED"}),
            ),
        )
        ids = list(
            await session.scalars(
                select(GroupCheckEvent.id)
                .where(ready, GroupCheckEvent.available_at <= now())
                .order_by(GroupCheckEvent.available_at, GroupCheckEvent.id)
                .limit(limit)
            )
        )
    for identifier in ids:
        try:
            await _process(sessions, bot, identifier)
        except DomainError as error:
            # Removed/disabled groups cannot retain an endlessly runnable head item.
            async with sessions() as session:
                await session.execute(
                    update(GroupCheckEvent)
                    .where(
                        GroupCheckEvent.id == identifier,
                        GroupCheckEvent.state.in_({"PENDING", "WAITING", "QUEUED"}),
                    )
                    .values(state="CANCELLED", result=error.code)
                )
                await session.commit()
        except Exception as error:
            structlog.get_logger().warning(
                "checks_event_deferred", event_id=identifier, exception_type=type(error).__name__
            )
    return len(ids)

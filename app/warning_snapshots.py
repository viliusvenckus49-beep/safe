"""Immutable warning facts in the existing append-only moderation audit."""

from sqlalchemy import BigInteger, cast, func, select

from app.models import AuditEvent, GroupModerationSettings, GroupSanction


async def current_counts(session, chat, target):
    policy = await session.get(GroupModerationSettings, chat)
    count = await session.scalar(
        select(func.count())
        .select_from(GroupSanction)
        .where(
            GroupSanction.chat_id == chat,
            GroupSanction.telegram_id == target,
            GroupSanction.kind == "WARN",
            GroupSanction.status == "RECORDED",
        )
    )
    return int(count or 0), policy.warning_limit if policy is not None else 3


async def warning_snapshot(session, sanction):
    event = await session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.action == "group_moderation_requested",
            cast(AuditEvent.details["sanction_id"].as_string(), BigInteger) == sanction.id,
        )
        .order_by(AuditEvent.id)
        .limit(1)
    )
    if event is not None:
        details = event.details
        count, limit = details.get("warning_count"), details.get("warning_limit")
        if type(count) is int and count > 0 and type(limit) is int and 2 <= limit <= 6:
            return count, limit, details.get("warning_action", "OFF")
    # Legacy rows have no snapshot. Reconstruct known cancellations/consumption
    # before this event, never count only the warnings that happen to be live now.
    prior = await session.scalars(
        select(AuditEvent).where(
            AuditEvent.action.in_(
                ("group_moderation_warnings_cancelled", "group_moderation_warnings_consumed")
            ),
            cast(AuditEvent.details["chat_id"].as_string(), BigInteger) == sanction.chat_id,
            cast(AuditEvent.details["user_id"].as_string(), BigInteger) == sanction.telegram_id,
            AuditEvent.created_at <= sanction.created_at,
        )
    )
    excluded = {identifier for e in prior for identifier in e.details.get("warning_ids", [])}
    ids = await session.scalars(
        select(GroupSanction.id).where(
            GroupSanction.chat_id == sanction.chat_id,
            GroupSanction.telegram_id == sanction.telegram_id,
            GroupSanction.kind == "WARN",
            GroupSanction.id <= sanction.id,
        )
    )
    policy = await session.get(GroupModerationSettings, sanction.chat_id)
    return (
        sum(identifier not in excluded for identifier in ids),
        policy.warning_limit if policy else 3,
        policy.warning_action if policy else "OFF",
    )

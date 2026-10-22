"""Per-group, evidence-based checks. Unknown evidence never authorizes punishment."""

import asyncio
import hashlib
import unicodedata
from typing import Any

from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import DomainError
from app.group_authorization import GroupAuthorizer
from app.i18n import language
from app.models import (
    GroupCheckEvent,
    GroupCheckMember,
    GroupCheckSettings,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    now,
)
from app.moderation_services import audit, lock_group, module_enabled, utc

OBLIGATIONS = ("surname", "username", "photo")
NAME_BLOCKS = ("arabic", "han", "cyrillic", "spam")
RULES = OBLIGATIONS + NAME_BLOCKS
ACTIONS = ("OFF", "ADVISE", "WARN", "KICK", "MUTE", "BAN")
API_TIMEOUT = 5


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def is_member(member: Any) -> bool:
    return member.status in {"member", "administrator", "creator"} or (
        member.status == "restricted" and getattr(member, "is_member", None) is True
    )


def script_matches(name: str, rule: str) -> bool:
    for char in name:
        label = unicodedata.name(char, "")
        if not unicodedata.category(char).startswith("L"):
            continue
        if rule == "arabic" and "ARABIC" in label:
            return True
        if rule == "han" and label.startswith(
            ("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH")
        ):
            return True
        if rule == "cyrillic" and "CYRILLIC" in label:
            return True
    return False


async def configure(session, bot, chat, actor, version, **changes):
    """Separate policy CAS; never silently enable moderation modules."""
    await GroupAuthorizer(bot).actor(chat, actor, configure=True)
    allowed = {
        "actions",
        "check_on_join",
        "delete_messages",
        "notify_result",
        "mute_seconds",
        "spam_terms",
    }
    if not changes.keys() <= allowed:
        raise DomainError("checks_invalid")
    try:
        group = await lock_group(session, chat)
        policy = await session.get(GroupCheckSettings, chat, populate_existing=True)
        if (policy.version if policy else 0) != version:
            raise DomainError("checks_stale")
        if policy is None:
            policy = GroupCheckSettings(
                chat_id=chat,
                actions={},
                spam_terms=[],
                updated_by=actor,
                version=0,
                check_on_join=True,
                delete_messages=False,
                notify_result=True,
                mute_seconds=86400,
            )
        values = {field: changes.get(field, getattr(policy, field)) for field in allowed}
        actions = values["actions"]
        if (
            not isinstance(actions, dict)
            or not actions.keys() <= set(RULES)
            or any(v not in ACTIONS for v in actions.values())
        ):
            raise DomainError("checks_invalid")
        if any(
            type(values[k]) is not bool
            for k in ("check_on_join", "delete_messages", "notify_result")
        ):
            raise DomainError("checks_invalid")
        if type(values["mute_seconds"]) is not int or not 300 <= values["mute_seconds"] <= 2592000:
            raise DomainError("checks_invalid")
        terms = values["spam_terms"]
        if (
            not isinstance(terms, list)
            or len(terms) > 20
            or any(not isinstance(s, str) or not 2 <= len(s.strip()) <= 50 for s in terms)
        ):
            raise DomainError("checks_invalid")
        if actions.get("spam", "OFF") != "OFF" and not terms:
            raise DomainError("checks_terms_required")
        rights = await GroupAuthorizer(bot).member(chat, bot.id)
        if rights.status != "administrator" or (
            values["delete_messages"] and getattr(rights, "can_delete_messages", False) is not True
        ):
            raise DomainError("group_moderation_bot_rights")
        native = {v for v in actions.values() if v in {"WARN", "KICK", "MUTE", "BAN"}}
        moderation = await session.get(GroupModerationSettings, chat)
        if any(moderation is None or not module_enabled(moderation, v) for v in native):
            raise DomainError("checks_modules")
        if native and getattr(rights, "can_restrict_members", False) is not True:
            raise DomainError("group_moderation_bot_rights")
        if native & {"MUTE", "KICK"} and group.chat_type != "supergroup":
            raise DomainError("group_moderation_supergroup_required")
        if (
            "MUTE" in native
            and moderation
            and values["mute_seconds"] > moderation.max_duration_seconds
        ):
            raise DomainError("group_moderation_invalid_duration")
        # Executing native actions cannot be revoked by changing a screen.
        busy = await session.scalar(
            select(GroupSanctionJob.id)
            .join(GroupCheckEvent, GroupCheckEvent.sanction_id == GroupSanctionJob.sanction_id)
            .where(
                GroupCheckEvent.chat_id == chat,
                GroupSanctionJob.operation == "APPLY",
                GroupSanctionJob.status.in_({"PROCESSING", "UNCERTAIN"}),
            )
            .limit(1)
        )
        delivery = await session.scalar(
            select(GroupCheckEvent.id)
            .where(GroupCheckEvent.chat_id == chat, GroupCheckEvent.state == "SENDING")
            .limit(1)
        )
        if busy or delivery:
            raise DomainError("group_moderation_busy")
        for field, value in values.items():
            setattr(policy, field, value)
        policy.version += 1
        session.add(policy)
        policy.updated_by, policy.updated_at, policy.language = actor, now(), language.get()
        # Unclaimed native actions are fenced again at execution; preserve manual jobs.
        events = list(
            await session.scalars(
                select(GroupCheckEvent).where(
                    GroupCheckEvent.chat_id == chat,
                    GroupCheckEvent.state.in_({"PENDING", "WAITING", "QUEUED"}),
                )
            )
        )
        for event in events:
            event.state, event.result = "CANCELLED", "SETTINGS_CHANGED"
            if event.sanction_id:
                sanction = await session.get(GroupSanction, event.sanction_id)
                if sanction and sanction.status == "PENDING":
                    sanction.status = "CANCELLED"
                    for job in await session.scalars(
                        select(GroupSanctionJob).where(
                            GroupSanctionJob.sanction_id == sanction.id,
                            GroupSanctionJob.status == "PENDING",
                        )
                    ):
                        job.status, job.result = "CANCELLED", "CHECK_SETTINGS_CHANGED"
        audit(
            session, "checks_settings", actor, chat, version=policy.version, fields=sorted(changes)
        )
        await session.commit()
        return policy
    except Exception:
        await session.rollback()
        raise


async def evidence(session: AsyncSession, bot, policy, user, member, *, only=None):
    """Return (violations, unknown). Photo requirement means publicly visible photo."""
    active = [
        r for r in RULES if policy.actions.get(r, "OFF") != "OFF" and (only is None or r == only)
    ]
    violations, unknown = [], []
    name = " ".join(filter(None, (user.first_name, user.last_name)))
    for rule in active:
        result = False
        try:
            if rule == "surname":
                result = not (user.last_name or "").strip()
            elif rule == "username":
                result = not user.username
            elif rule in {"arabic", "han", "cyrillic"}:
                result = script_matches(name, rule)
            elif rule == "spam":
                result = any(normalized(term) in normalized(name) for term in policy.spam_terms)
            elif rule == "photo":
                photos = await asyncio.wait_for(
                    bot.get_user_profile_photos(user.id, limit=1), API_TIMEOUT
                )
                result = photos.total_count == 0
            if result:
                violations.append(rule)
        except (TelegramAPIError, DomainError, TimeoutError, OSError, AttributeError):
            unknown.append(rule)
    return violations, unknown


async def policy_for_event(session, event):
    policy = await session.get(GroupCheckSettings, event.chat_id, populate_existing=True)
    if (
        policy is None
        or policy.version != event.version
        or (
            not policy.delete_messages or policy.actions.get(event.rule, "OFF") == "OFF"
            if event.action == "DELETE"
            else policy.actions.get(event.rule, "OFF") != event.action
        )
        or event.state not in {"PENDING", "WAITING", "QUEUED"}
    ):
        raise DomainError("checks_stale")
    return policy


async def guard_request(session, event_id, chat, target, kind):
    event = await session.get(GroupCheckEvent, event_id, populate_existing=True)
    if event is None or (event.chat_id, event.telegram_id, event.action) != (chat, target, kind):
        raise DomainError("checks_stale")
    await policy_for_event(session, event)
    if (
        not event.notice_message_id
        or event.state not in {"WAITING", "QUEUED"}
        or utc(event.available_at) > now()
    ):
        raise DomainError("checks_notice_required")
    return event


async def guard_job(session, bot, sanction, member):
    event = await session.scalar(
        select(GroupCheckEvent).where(GroupCheckEvent.sanction_id == sanction.id)
    )
    if event is None:
        return
    policy = await policy_for_event(session, event)
    bad, unknown = await evidence(session, bot, policy, member.user, member, only=event.rule)
    if unknown or event.rule not in bad:
        raise DomainError("checks_unconfirmed")


async def guard_version(session, sanction):
    event = await session.scalar(
        select(GroupCheckEvent).where(GroupCheckEvent.sanction_id == sanction.id)
    )
    if event is not None:
        await policy_for_event(session, event)


def key(*parts):
    return hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()


async def observe(sessions, bot, chat, user, *, message_id=None, joined_at=None, check_join=False):
    if user.is_bot:
        return False
    async with sessions() as session:
        policy = await session.get(GroupCheckSettings, chat)
        if policy is None or not any(v != "OFF" for v in policy.actions.values()):
            return False
        try:
            await lock_group(session, chat)
            await session.commit()
            current = await GroupAuthorizer(bot).member(chat, user.id)
            if (
                not is_member(current)
                or current.status in {"administrator", "creator"}
                or current.user.is_bot
            ):
                return False
            user = current.user  # A stale message profile never overrides the live identity.
            await lock_group(session, chat)
            policy = await session.get(GroupCheckSettings, chat, populate_existing=True)
            tracked = await session.get(GroupCheckMember, (chat, user.id))
            if tracked is None:
                tracked = GroupCheckMember(
                    chat_id=chat,
                    telegram_id=user.id,
                    joined_at=joined_at or now(),
                    checked_at=now(),
                    episodes={},
                )
                session.add(tracked)
                await session.flush()
            elif joined_at and utc(joined_at) > utc(tracked.joined_at):
                tracked.joined_at, tracked.episodes = joined_at, {}
            await session.commit()
            if check_join and not policy.check_on_join:
                return False
            bad, unknown = await evidence(session, bot, policy, user, current)
            version = policy.version
            await lock_group(session, chat)
            await session.refresh(policy)
            if policy.version != version:
                return False
            tracked = await session.get(GroupCheckMember, (chat, user.id), populate_existing=True)
            tracked.checked_at = now()
            episodes = dict(tracked.episodes)
            priority = {"ADVISE": 1, "WARN": 2, "MUTE": 3, "KICK": 4, "BAN": 5}
            selected = max(bad, key=lambda r: priority[policy.actions[r]], default=None)
            for rule in RULES:
                if policy.actions.get(rule, "OFF") == "OFF" or rule in unknown:
                    continue
                request = key(
                    chat,
                    user.id,
                    version,
                    rule,
                    utc(tracked.joined_at).isoformat(),
                    episodes.get(rule, 0),
                )
                previous = await session.scalar(
                    select(GroupCheckEvent).where(GroupCheckEvent.request_key == request)
                )
                if rule not in bad:
                    if previous and previous.state != "RESOLVED":
                        # Compliance ends the episode, but never undoes or rewrites
                        # a confirmed or already executing native punishment.
                        can_cancel = previous.state in {"PENDING", "WAITING", "SKIPPED"}
                        if previous.state == "QUEUED" and previous.sanction_id:
                            sanction = await session.get(GroupSanction, previous.sanction_id)
                            busy = await session.scalar(
                                select(GroupSanctionJob.id)
                                .where(
                                    GroupSanctionJob.sanction_id == previous.sanction_id,
                                    GroupSanctionJob.status.in_({"PROCESSING", "UNCERTAIN"}),
                                )
                                .limit(1)
                            )
                            can_cancel = (
                                sanction is not None
                                and sanction.status == "PENDING"
                                and busy is None
                            )
                        if can_cancel:
                            previous.state, previous.result = "RESOLVED", "NOW_COMPLIANT"
                        episodes[rule] = episodes.get(rule, 0) + 1
                    continue
                if previous is None:
                    session.add(
                        GroupCheckEvent(
                            chat_id=chat,
                            telegram_id=user.id,
                            display_name=user.full_name[:256],
                            rule=rule,
                            action=policy.actions[rule],
                            version=version,
                            request_key=request,
                            message_id=message_id,
                            state="PENDING" if rule == selected else "SKIPPED",
                            result=None if rule == selected else "STRONGER_RULE",
                        )
                    )
                elif rule == selected and previous.state == "SKIPPED" and previous.attempts < 5:
                    previous.state, previous.result = "PENDING", None
            tracked.episodes = episodes
            for rule in unknown:
                request = key(
                    "unknown", chat, user.id, version, rule, int(now().timestamp()) // 300
                )
                if not await session.scalar(
                    select(GroupCheckEvent.id).where(GroupCheckEvent.request_key == request)
                ):
                    session.add(
                        GroupCheckEvent(
                            chat_id=chat,
                            telegram_id=user.id,
                            display_name=user.full_name[:256],
                            rule=rule,
                            action=policy.actions[rule],
                            version=version,
                            request_key=request,
                            state="SKIPPED",
                            result="EVIDENCE_UNAVAILABLE",
                        )
                    )
            delete = bool(bad and policy.delete_messages and message_id)
            if delete:
                request = key("delete", chat, message_id)
                if not await session.scalar(
                    select(GroupCheckEvent.id).where(GroupCheckEvent.request_key == request)
                ):
                    session.add(
                        GroupCheckEvent(
                            chat_id=chat,
                            telegram_id=user.id,
                            display_name=user.full_name[:256],
                            rule=bad[0],
                            action="DELETE",
                            version=version,
                            request_key=request,
                            message_id=message_id,
                        )
                    )
            await session.commit()
            return bool(bad)
        except (TelegramAPIError, DomainError, TimeoutError, OSError):
            await session.rollback()
            return False

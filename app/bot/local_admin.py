"""ID/reply/verified-username commands and live-authorized administration panels."""

import asyncio
import hashlib
import re
from datetime import timedelta
from html import escape
from typing import Any
from weakref import WeakValueDictionary

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.types import User as TelegramUser
from sqlalchemy import or_, select, update

from app.bot.callbacks import LocalAction
from app.bot.keyboards import keyboard
from app.bot.local_moderation import LocalModerationExecutor
from app.bot.moderation_errors import (
    basic_kind,
    command_name,
    deliver_error,
    diagnose_target,
    error_message,
    target_read,
)
from app.bot.moderation_messages import success_message
from app.bot.states import LocalModerationFlow
from app.bot.validation import actor_id
from app.errors import DomainError
from app.group_authorization import numeric_id
from app.group_services import GroupService
from app.i18n import language, t, use_language
from app.models import (
    GroupModerationDraft,
    GroupModerationSettings,
    GroupSanction,
    GroupSanctionJob,
    ManagedGroup,
    now,
)
from app.moderation_services import GroupModerationService, utc
from app.services import Service

PANEL_LOCKS: WeakValueDictionary[tuple[int, int], asyncio.Lock] = WeakValueDictionary()


def panel_lock(chat: int, message: int) -> asyncio.Lock:
    key = (chat, message)
    lock = PANEL_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        PANEL_LOCKS[key] = lock
    return lock


KINDS = ("BAN", "UNBAN", "MUTE", "UNMUTE", "WARN", "KICK")
DURATIONS = {"30m": 1800, "1h": 3600, "1d": 86400, "7d": 604800, "30d": 2592000}
FOREVER = {"forever", "permanent", "neribotai", "навсегда", "0"}
SWITCHES = (
    "enabled",
    "bans_enabled",
    "mutes_enabled",
    "warnings_enabled",
    "kicks_enabled",
    "allow_permanent_bans",
    "allow_permanent_mutes",
)


def cb(action: str, chat: int = 0, value: str = "") -> str:
    return LocalAction(action=action, chat=chat, value=value).pack()


def nav(chat: int = 0) -> InlineKeyboardMarkup:
    return keyboard([[(t("lm.back"), cb("group", chat) if chat else cb("groups"))]])


def parse_target(text: str, replied: Message | None, kind: str) -> tuple[int, Any, str]:
    parts = text.strip().split(maxsplit=1)
    if parts and parts[0].isascii() and parts[0].isdigit():
        target = int(parts[0])
        rest = parts[1] if len(parts) > 1 else ""
    elif replied and replied.from_user and not replied.sender_chat:
        target, rest = replied.from_user.id, text.strip()
    else:
        raise DomainError("group_moderation_invalid_target")
    numeric_id(target)
    if rest.startswith("@"):
        raise DomainError("local_invalid")
    head = rest.split(maxsplit=1)
    duration, reason = None, rest
    if head and (head[0].lower() in DURATIONS or head[0].lower() in FOREVER):
        if kind not in {"BAN", "MUTE"}:
            raise DomainError("local_invalid")
        seconds = DURATIONS.get(head[0].lower())
        duration = (now() + timedelta(seconds=seconds)).replace(microsecond=0) if seconds else None
        reason = head[1] if len(head) > 1 else ""
    elif head and re.fullmatch(r"\d+[a-zA-Z]+", head[0]):
        raise DomainError("local_invalid")
    if len(reason) > 1000:
        raise DomainError("local_invalid")
    return target, duration, reason


async def parse_target_input(
    local: GroupModerationService,
    chat: int,
    actor: int,
    text: str,
    replied: Message | None,
    kind: str,
) -> tuple[int, Any, str]:
    parts = text.strip().split(maxsplit=1)
    if parts and parts[0].startswith("@"):
        target = await local.resolve_username(chat, actor, parts[0])
        text = str(target) + (" " + parts[1] if len(parts) > 1 else "")
        replied = None  # An explicit target never silently falls back to a reply.
    return parse_target(text, replied, kind)


def error_text(error: Exception) -> str:
    if not isinstance(error, DomainError):
        return t("lm.error")
    key = {
        "local_invalid": "invalid",
        "group_moderation_invalid_id": "invalid",
        "group_moderation_invalid_username": "invalid",
        "group_moderation_username_unknown": "username_unknown",
        "group_moderation_username_changed": "username_changed",
        "group_moderation_invalid_duration": "disabled",
        "group_moderation_disabled": "disabled",
        "group_moderation_invalid_draft": "stale",
        "group_moderation_not_found": "not_found",
        "group_moderation_scam_protected": "protected",
        "group_moderation_forbidden": "permission",
        "group_moderation_uncertain": "uncertain",
    }.get(error.code, "error")
    return t("lm." + key)


async def command_error(
    message: Message,
    service: Service,
    error: Exception,
    *,
    command: str,
    local: GroupModerationService | None,
    chat: int | None,
    username: str | None,
    may_have_executed: bool = False,
) -> None:
    try:
        actor = actor_id(message)
    except DomainError:
        await deliver_error(message, None, "", command)
        return
    kind = basic_kind(error)
    target = target_read(error, chat, actor, bound_bot(message).id) if chat is not None else None
    if target is not None and local is not None and chat is not None:
        kind = (
            "check_unavailable"
            if may_have_executed
            else await diagnose_target(local, chat, actor, target, username=username)
        )
    if kind is None and not isinstance(error, DomainError):
        kind = "check_unavailable"
    text = error_message(command, kind, fallback=error_text(error))
    await deliver_error(message, actor, text, command)


async def access(
    service: Service, bot: Any, chat: int, actor: int, *, configure: bool = False
) -> GroupModerationService:
    local = GroupModerationService(
        service.session,
        bot,
        use_mtproto_lookup=(
            service.settings.group_help_enabled or service.settings.moderation_mtproto_enabled
        ),
    )
    await local._check_group(chat)
    await local.authorization.actor(chat, actor, configure=configure)
    return local


async def display(
    message: Message, text: str, markup: InlineKeyboardMarkup | None, *, edit: bool = False
) -> Message:
    if edit:
        try:
            async with panel_lock(message.chat.id, message.message_id):
                result = await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return result if isinstance(result, Message) else message
        except TelegramBadRequest as error:
            if "message is not modified" in error.message.lower():
                return message
            raise
    return await message.answer(text, reply_markup=markup, parse_mode="HTML")


def bound_bot(message: Message) -> Any:
    if message.bot is None:
        raise DomainError("group_moderation_forbidden")
    return message.bot


async def private_panel(
    message: Message, actor: int, text: str, markup: InlineKeyboardMarkup
) -> Message | None:
    try:
        return await bound_bot(message).send_message(
            actor, text, reply_markup=markup, parse_mode="HTML"
        )
    except TelegramAPIError:
        me = await bound_bot(message).me()
        await message.answer(
            t("lm.open_private"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text=t("lm.open_private_button"), url=f"https://t.me/{me.username}"
                        )
                    ]
                ]
            ),
        )
        return None


async def group_panel(
    message: Message,
    state: FSMContext,
    service: Service,
    actor: int,
    chat: int,
    *,
    edit: bool = False,
) -> None:
    local = await access(service, bound_bot(message), chat, actor)
    group = await service.session.get(ManagedGroup, chat)
    await state.set_state(None)
    await state.update_data(lm_chat=chat)
    rows = [
        [(t("lm." + a), cb("action", chat, a)), (t("lm." + b), cb("action", chat, b))]
        for a, b in (("BAN", "UNBAN"), ("MUTE", "UNMUTE"), ("WARN", "KICK"))
    ]
    rows += [
        [(t("lm.history"), cb("action", chat, "HISTORY"))],
        [(t("lm.warnings"), cb("action", chat, "WARNINGS"))],
    ]
    try:
        await local.authorization.actor(chat, actor, configure=True)
        rows += [[(t("lm.settings"), cb("settings", chat))]]
    except DomainError:
        pass
    rows += [[(t("lm.back"), cb("groups"))]]
    title = t("lm.title") + "\n\n<b>" + escape(group.title if group else str(chat)) + "</b>"
    if message.chat.type != "private":
        await private_panel(message, actor, title, keyboard(rows))
    else:
        await display(message, title, keyboard(rows), edit=edit)


async def groups_panel(
    message: Message, service: Service, actor: int, *, edit: bool = False, page: int = 0
) -> None:
    groups = list(
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

    async def permitted(group: ManagedGroup) -> bool:
        async with semaphore:
            try:
                await GroupModerationService(
                    service.session, bound_bot(message)
                ).authorization.actor(group.chat_id, actor)
                return True
            except (DomainError, TelegramAPIError, TimeoutError, OSError):
                return False

    allowed = [
        g
        for g, allowed in zip(
            groups, await asyncio.gather(*(permitted(g) for g in groups)), strict=True
        )
        if allowed
    ]
    rows = [[(g.title[:48], cb("group", g.chat_id))] for g in allowed[page * 10 : page * 10 + 10]]
    if rows:
        if page:
            rows.append([(t("lm.back"), cb("groups", value=str(page - 1)))])
        if len(allowed) > page * 10 + 10:
            rows.append([(t("lm.next"), cb("groups", value=str(page + 1)))])
    await display(
        message,
        t("lm.title") + "\n\n" + t("lm.groups" if rows else "lm.empty"),
        keyboard(rows),
        edit=edit,
    )


async def settings_panel(message: Message, service: Service, actor: int, chat: int) -> None:
    await access(service, bound_bot(message), chat, actor, configure=True)
    policy = await service.session.get(GroupModerationSettings, chat)
    version = policy.version if policy else 0
    rows = [
        [
            (
                t("lm." + key)
                + " · "
                + t("lm.on" if policy and getattr(policy, key) else "lm.off"),
                cb("toggle", chat, f"{key}:{version}"),
            )
        ]
        for key in SWITCHES
    ]
    rows.append([(t("lm.back"), cb("group", chat))])
    await display(
        message, t("lm.title") + "\n\n" + t("lm.settings_hint"), keyboard(rows), edit=True
    )


async def history_panel(
    message: Message,
    service: Service,
    actor: int,
    chat: int,
    target: int,
    *,
    warnings: bool = False,
    offset: int = 0,
    edit: bool = True,
) -> None:
    await access(service, bound_bot(message), chat, actor)
    numeric_id(target)
    query = select(GroupSanction).where(
        GroupSanction.chat_id == chat, GroupSanction.telegram_id == target
    )
    if warnings:
        query = query.where(GroupSanction.kind == "WARN")
    rows = list(
        await service.session.scalars(
            query.order_by(GroupSanction.id.desc()).offset(offset).limit(6)
        )
    )
    text = (
        t("lm.title")
        + f"\n\n🆔 <code>{target}</code>\n\n"
        + t("lm.warnings" if warnings else "lm.history")
    )
    for row in rows[:5]:
        text += f"\n\n<b>#{row.id} · {t('lm.' + row.kind)}</b>\n{utc(row.created_at).strftime('%Y-%m-%d %H:%M UTC')} · {t('lm.state.' + row.status)}\n🆔 <code>{row.actor_id}</code>"
        if row.reason:
            text += "\n" + t("lm.reason", reason=escape(row.reason[:120]))
        release = await service.session.scalar(
            select(GroupModerationDraft)
            .where(
                GroupModerationDraft.sanction_id == row.id,
                GroupModerationDraft.status == "CONFIRMED",
                GroupModerationDraft.kind.in_(("UNBAN", "UNMUTE")),
            )
            .order_by(GroupModerationDraft.created_at.desc(), GroupModerationDraft.id.desc())
            .limit(1)
        )
        if release:
            text += (
                "\n"
                + t("lm.last_release")
                + f" {t('lm.' + release.kind)} · {utc(release.created_at).strftime('%Y-%m-%d %H:%M UTC')} · <code>{release.actor_id}</code>"
            )
            if release.reason:
                text += "\n" + t("lm.reason", reason=escape(release.reason[:120]))
    if not rows:
        text += "\n\n" + t("lm.no_history")
    buttons = []
    if offset:
        buttons.append(
            [
                (
                    t("lm.back"),
                    cb("whist" if warnings else "hist", chat, f"{target}:{max(0, offset - 5)}"),
                )
            ]
        )
    if len(rows) > 5:
        buttons.append(
            [(t("lm.next"), cb("whist" if warnings else "hist", chat, f"{target}:{offset + 5}"))]
        )
    buttons.append([(t("lm.back"), cb("group", chat))])
    await display(message, text, keyboard(buttons), edit=edit)


async def receipt_text(
    session: Any, draft: GroupModerationDraft, *, observed: tuple[TelegramUser, ...] = ()
) -> tuple[str, bool]:
    row = await session.get(GroupSanction, draft.sanction_id, populate_existing=True)
    if row is None:
        return t("lm.failed"), True
    jobs = list(
        await session.scalars(
            select(GroupSanctionJob).where(GroupSanctionJob.sanction_id == row.id)
        )
    )
    release = next((j for j in jobs if j.operation == "RELEASE"), None)
    apply = next((j for j in jobs if j.operation == "APPLY"), None)
    key, done = "queued", False
    if row.status == "RECORDED":
        key, done = "recorded", True
    elif row.status == "UNCERTAIN":
        key, done = "uncertain", True
    elif draft.kind in {"UNBAN", "UNMUTE"} or row.kind == "KICK":
        if release and release.result in {"SCAM_PROTECTED", "NOT_OWNED", "RESTRICTION_CHANGED"}:
            key, done = "protected", True
        elif release and release.result == "REVOKED_BEFORE_APPLY":
            key, done = "cancelled", True
        elif release and release.status == "SUCCEEDED":
            key, done = ("cancelled" if release.result == "NOT_APPLIED" else "done"), True
        elif release and release.status in {"FAILED", "CANCELLED"}:
            key, done = ("kick_incomplete" if row.kind == "KICK" and row.owned else "failed"), True
        elif row.status in {"FAILED", "CANCELLED"}:
            key, done = "failed", True
    elif apply and apply.status == "SUCCEEDED":
        key, done = ("already" if apply.result == "ALREADY_BANNED" else "done"), True
    elif row.status in {"FAILED", "CANCELLED"}:
        key, done = "failed", True
    if key in {"done", "recorded"}:
        return await success_message(session, draft, row, observed=observed), done
    text = (
        t("lm.title")
        + f"\n\n<b>{t('lm.' + draft.kind)}</b>\n🆔 <code>{draft.telegram_id}</code>\n\n"
        + t("lm." + key)
    )
    if draft.reason:
        text += "\n\n" + t("lm.reason", reason=escape(draft.reason))
    return text, done


async def show_draft(
    message: Message, service: Service, draft: GroupModerationDraft, *, edit: bool = False
) -> None:
    if draft.status == "CONFIRMED":
        text, _ = await receipt_text(service.session, draft)
    elif draft.status == "CANCELLED":
        text = t("lm.cancelled")
    else:
        text = (
            t("lm.title")
            + "\n\n"
            + t("lm.preview")
            + f"\n\n<b>{t('lm.' + draft.kind)}</b>\n🆔 <code>{draft.telegram_id}</code>"
        )
        if draft.kind in {"BAN", "MUTE"}:
            text += "\n" + (
                t("lm.duration", until=utc(draft.until_date).strftime("%Y-%m-%d %H:%M UTC"))
                if draft.until_date
                else t("lm.forever")
            )
        if draft.reason:
            text += "\n\n" + t("lm.reason", reason=escape(draft.reason))
        if draft.kind == "KICK":
            text += "\n\n" + t("lm.kick_notice")
    buttons = [
        [
            (t("lm.confirm"), cb("confirm", value=draft.id)),
            (t("lm.cancel"), cb("cancel", value=draft.id)),
        ],
        [(t("lm.back"), cb("group", draft.chat_id))],
    ]
    # Confirmation controls belong to the private preview, never to a receipt.
    result = await display(
        message, text, keyboard(buttons) if draft.status == "PENDING" else None, edit=edit
    )
    draft.panel_chat_id, draft.panel_message_id = result.chat.id, result.message_id
    draft.language = language.get()
    draft.receipt_hash = hashlib.sha256(text.encode()).hexdigest()
    draft.receipt_done = False
    await service.session.commit()


async def publish_receipts(sessions: Any, bot: Any, *, limit: int = 10) -> None:
    async with sessions() as session:
        drafts = list(
            await session.scalars(
                select(GroupModerationDraft)
                .join(GroupSanction, GroupSanction.id == GroupModerationDraft.sanction_id)
                .where(
                    GroupModerationDraft.status == "CONFIRMED",
                    GroupModerationDraft.panel_message_id.is_not(None),
                    GroupModerationDraft.receipt_done.is_(False),
                    or_(
                        GroupModerationDraft.receipt_available_at.is_(None),
                        GroupModerationDraft.receipt_available_at <= now(),
                    ),
                    or_(
                        GroupModerationDraft.receipt_checked_at.is_(None),
                        GroupSanction.updated_at > GroupModerationDraft.receipt_checked_at,
                    ),
                )
                .order_by(GroupSanction.updated_at)
                .limit(limit)
            )
        )
        await session.commit()
        for draft in drafts:
            async with panel_lock(draft.panel_chat_id, draft.panel_message_id):
                await session.refresh(draft)
                if draft.receipt_done:
                    continue
                with use_language(draft.language):
                    text, done = await receipt_text(session, draft)
                    digest = hashlib.sha256(text.encode()).hexdigest()
                    row = await session.get(GroupSanction, draft.sanction_id)
                    checked_at = row.updated_at
                    try:
                        if digest != draft.receipt_hash:
                            await bot.edit_message_text(
                                chat_id=draft.panel_chat_id,
                                message_id=draft.panel_message_id,
                                text=text,
                                parse_mode="HTML",
                            )
                        draft.receipt_hash, draft.receipt_done, draft.receipt_checked_at = (
                            digest,
                            done,
                            checked_at,
                        )
                    except TelegramRetryAfter as error:
                        draft.receipt_available_at = now() + timedelta(seconds=error.retry_after)
                    except TelegramBadRequest as error:
                        if "message is not modified" in error.message.lower():
                            draft.receipt_hash, draft.receipt_done, draft.receipt_checked_at = (
                                digest,
                                done,
                                checked_at,
                            )
                        else:
                            draft.receipt_done = (
                                True  # Never recreate missing receipts or spam a group.
                            )
                    except (TelegramAPIError, TimeoutError, OSError):
                        draft.receipt_available_at = now() + timedelta(seconds=30)
                    await session.commit()


def register_local_handlers(router: Router) -> None:
    @router.message(Command("moderation"))
    async def open_local(message: Message, state: FSMContext, service: Service) -> None:
        try:
            await state.clear()
            actor = actor_id(message)
            args = (message.text or "").split(maxsplit=1)
            chat = (
                message.chat.id
                if message.chat.type != "private"
                else (int(args[1]) if len(args) > 1 else None)
            )
            if chat:
                await group_panel(message, state, service, actor, chat)
            else:
                await groups_panel(message, service, actor)
        except (DomainError, TelegramAPIError, TimeoutError, OSError, ValueError) as error:
            await service.session.rollback()
            await message.answer(error_text(error))

    @router.message(Command("ban", "unban", "mute", "unmute", "warn", "kick", "warnings"))
    async def local_command(
        message: Message, state: FSMContext, service: Service, session_factory: Any
    ) -> None:
        local, chat, username = None, None, None
        may_have_executed = False
        called = command_name(message.text or "")
        try:
            actor = actor_id(message)
            command, *args = (message.text or "").split(maxsplit=1)
            kind = command.split("@", 1)[0][1:].upper()
            data = await state.get_data()
            selected = message.chat.id if message.chat.type != "private" else data.get("lm_chat")
            chat = int(selected) if selected else None
            if not chat:
                await groups_panel(message, service, actor)
                return
            first = args[0].split(maxsplit=1)[0] if args and args[0].strip() else ""
            username = first if first.startswith("@") else None
            local = await access(service, bound_bot(message), int(chat), actor)
            target, until, reason = await parse_target_input(
                local, int(chat), actor, args[0] if args else "", message.reply_to_message, kind
            )
            if kind == "WARNINGS":
                panel = (
                    message
                    if message.chat.type == "private"
                    else await private_panel(message, actor, t("lm.title"), nav(int(chat)))
                )
                if panel is not None:
                    await history_panel(
                        panel,
                        service,
                        actor,
                        int(chat),
                        target,
                        warnings=True,
                        edit=message.chat.type != "private",
                    )
                return
            draft = await local.prepare(
                int(chat),
                actor,
                target,
                kind,
                until_date=until,
                reason=reason,
                request=f"local:{message.chat.id}:{message.message_id}:{actor}",
            )
            # Explicit textual commands are the administrator's direct instruction.
            # Menu-selected actions instead require a durable confirmation below.
            may_have_executed = True
            sanction = await local.confirm(draft.id, actor)
            await LocalModerationExecutor(session_factory, bound_bot(message)).process(
                limit=2, sanction_id=sanction.id
            )
            await service.session.refresh(draft)
            people = tuple(
                person
                for person in (
                    message.from_user,
                    message.reply_to_message.from_user if message.reply_to_message else None,
                )
                if person is not None
            )
            text, done = await receipt_text(service.session, draft, observed=people)
            if draft.panel_message_id:
                try:
                    await bound_bot(message).edit_message_text(
                        chat_id=draft.panel_chat_id,
                        message_id=draft.panel_message_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except TelegramBadRequest as error:
                    if "message is not modified" not in error.message.lower():
                        raise
            else:
                panel = await message.answer(text, parse_mode="HTML")
                draft.panel_chat_id, draft.panel_message_id = panel.chat.id, panel.message_id
            draft.language, draft.receipt_done = language.get(), done
            draft.receipt_hash = hashlib.sha256(text.encode()).hexdigest()
            await service.session.commit()
        except (DomainError, TelegramAPIError, TimeoutError, OSError, ValueError) as error:
            await service.session.rollback()
            await command_error(
                message,
                service,
                error,
                command=called,
                local=local,
                chat=chat,
                username=username,
                may_have_executed=may_have_executed,
            )

    @router.message(LocalModerationFlow.target, F.text, ~F.text.startswith("/"))
    async def local_input(message: Message, state: FSMContext, service: Service) -> None:
        local, chat, username = None, None, None
        called = "/moderation"
        try:
            if message.chat.type != "private":
                raise DomainError("group_moderation_forbidden")
            data = await state.get_data()
            chat, kind = int(data["lm_chat"]), data["lm_kind"]
            called = command_name(kind)
            first = (message.text or "").split(maxsplit=1)
            username = first[0] if first and first[0].startswith("@") else None
            local = await access(service, bound_bot(message), chat, actor_id(message))
            target, until, reason = await parse_target_input(
                local, chat, actor_id(message), message.text or "", None, kind
            )
            panel = message.model_copy(
                update={"message_id": data.get("lm_panel", message.message_id)}
            ).as_(bound_bot(message))
            if kind in {"HISTORY", "WARNINGS"}:
                await state.set_state(None)
                await history_panel(
                    panel, service, actor_id(message), chat, target, warnings=kind == "WARNINGS"
                )
                return
            draft = await local.prepare(
                chat,
                actor_id(message),
                target,
                kind,
                until_date=until,
                reason=reason,
                request=f"local:{message.chat.id}:{message.message_id}:{actor_id(message)}",
            )
            await state.set_state(None)
            await show_draft(panel, service, draft, edit=True)
        except (
            DomainError,
            TelegramAPIError,
            TimeoutError,
            OSError,
            ValueError,
            KeyError,
        ) as error:
            await service.session.rollback()
            await command_error(
                message, service, error, command=called, local=local, chat=chat, username=username
            )

    @router.callback_query(LocalAction.filter())
    async def local_callback(
        callback: CallbackQuery,
        callback_data: LocalAction,
        state: FSMContext,
        service: Service,
        session_factory: Any,
    ) -> None:
        acknowledged = False
        chat = callback_data.chat
        try:
            if not isinstance(callback.message, Message) or callback.message.chat.type != "private":
                raise DomainError("group_moderation_forbidden")
            message, actor, action, value = (
                callback.message,
                callback.from_user.id,
                callback_data.action,
                callback_data.value,
            )
            if message.chat.id != actor:
                raise DomainError("group_moderation_forbidden")
            if action in {"confirm", "cancel", "refresh"}:
                draft = await service.session.get(
                    GroupModerationDraft, value, populate_existing=True
                )
                if draft is None or draft.actor_id != actor:
                    raise DomainError("group_moderation_invalid_draft")
                chat = draft.chat_id
            if action != "groups":
                local = await access(service, callback.bot, chat, actor)
            if action not in {"confirm", "refresh"}:
                # Navigating away retires background edits of this shared panel.
                await service.session.execute(
                    update(GroupModerationDraft)
                    .where(
                        GroupModerationDraft.panel_chat_id == message.chat.id,
                        GroupModerationDraft.panel_message_id == message.message_id,
                    )
                    .values(receipt_done=True)
                )
                await service.session.commit()
            await callback.answer()
            acknowledged = True
            if action == "groups":
                await state.set_state(None)
                await state.update_data(lm_chat=None, lm_kind=None)
                await groups_panel(
                    message, service, actor, edit=True, page=max(0, min(10000, int(value or 0)))
                )
            elif action == "group":
                await group_panel(message, state, service, actor, chat, edit=True)
            elif action == "settings":
                await settings_panel(message, service, actor, chat)
            elif action == "toggle":
                field, expected = value.split(":", 1)
                if field not in SWITCHES:
                    raise DomainError("local_invalid")
                policy = await service.session.get(
                    GroupModerationSettings, chat, populate_existing=True
                )
                current = {key: bool(policy and getattr(policy, key)) for key in SWITCHES}
                current[field] = not current[field]
                await local.configure(
                    chat,
                    actor,
                    **current,
                    expected_version=int(expected),
                    max_duration_seconds=policy.max_duration_seconds if policy else 2592000,
                )
                await settings_panel(message, service, actor, chat)
            elif action == "action":
                if value not in {*KINDS, "HISTORY", "WARNINGS"}:
                    raise DomainError("local_invalid")
                await state.set_state(LocalModerationFlow.target)
                await state.update_data(lm_chat=chat, lm_kind=value, lm_panel=message.message_id)
                await display(
                    message,
                    t("lm.title")
                    + "\n\n"
                    + t("lm." + value if value in KINDS else "lm.history")
                    + "\n\n"
                    + t("lm.target"),
                    nav(chat),
                    edit=True,
                )
            elif action in {"hist", "whist"}:
                target, offset = value.split(":", 1)
                await history_panel(
                    message,
                    service,
                    actor,
                    chat,
                    int(target),
                    warnings=action == "whist",
                    offset=max(0, min(10000, int(offset))),
                )
            elif action == "cancel":
                draft = await local.cancel_draft(value, actor)
                await display(message, t("lm.cancelled"), None, edit=True)
            elif action in {"confirm", "refresh"}:
                assert draft is not None
                if action == "confirm":
                    sanction = await local.confirm(value, actor)
                    await display(message, t("lm.queued"), None, edit=True)
                    await LocalModerationExecutor(session_factory, callback.bot).process(
                        limit=2, sanction_id=sanction.id
                    )
                await service.session.refresh(draft)
                if draft.status != "CONFIRMED":
                    raise DomainError("group_moderation_invalid_draft")
                await show_draft(message, service, draft, edit=True)
            else:
                raise DomainError("local_invalid")
        except (DomainError, TelegramAPIError, TimeoutError, OSError, ValueError) as error:
            await service.session.rollback()
            if not acknowledged:
                await callback.answer(error_text(error), show_alert=True)
            elif isinstance(callback.message, Message):
                await display(callback.message, error_text(error), None, edit=True)
            structlog.get_logger().info(
                "local_moderation_ui_rejected",
                user_id=callback.from_user.id,
                error_type=type(error).__name__,
            )

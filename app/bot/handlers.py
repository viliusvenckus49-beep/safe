import re
from typing import Any
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ForceReply, InlineKeyboardMarkup, Message

from app import presentation as p
from app.bot import keyboards as kb
from app.bot import redsafe_profile as rp
from app.bot.administrators import register_administrator_handlers
from app.bot.callbacks import (
    Action,
    Language,
    Moderation,
    ReportStep,
)
from app.bot.commands import user_commands
from app.bot.groups import register_group_handlers
from app.bot.middleware import ServiceMiddleware
from app.bot.rep_vote import begin as begin_vote
from app.bot.rep_vote import register_vote_handlers
from app.bot.reputation import register_reputation_handlers
from app.bot.scam_admin import (
    controls as scam_controls,
)
from app.bot.scam_admin import (
    listing as scam_listing,
)
from app.bot.scam_admin import (
    offer as scam_offer,
)
from app.bot.scam_admin import (
    refresh_controls as scam_refresh_controls,
)
from app.bot.scam_admin import (
    register_scam_handlers,
)
from app.bot.scam_notices import registered_scam_text
from app.bot.screens import (
    clear_flow,
    close_panel,
    flow_screen,
    preserved_source,
    render,
    send_screen,
)
from app.bot.states import AdminRepFlow, InputFlow, ReportFlow
from app.bot.trusted import open_panel as open_trusted_panel
from app.bot.trusted import register_trusted_handlers
from app.bot.validation import actor_id, message_content, valid_target
from app.i18n import language, t
from app.services import DomainError, Service

ADMIN_ACTIONS = {
    "trusted_admin",
    "admin",
    "admin_scams",
    "admin_target_back",
    "pending",
    "stats",
    "status",
    "audit",
    "users",
    "add_sc",
    "del_sc",
    "rep_admin",
    "rep_pending",
    "rep_review",
    "rep_add",
    "rep_sub",
    "rep_reset",
    "top_include",
    "top_exclude",
}

LINK = re.compile(r"^https://t\.me/(?:c/[0-9]+|[A-Za-z0-9_]+)/[0-9]+$")


def target_key(user: Any) -> str:
    return str(user.telegram_id) if user.telegram_id else f"@{user.username}"


def callback_target(user: Any) -> str:
    return str(user.telegram_id) if user.telegram_id else f"u:{user.id}"


async def target_from_message(message: Message, service: Service) -> tuple[str, str]:
    parts = message_content(message).split(maxsplit=2)
    # An explicit target takes priority over an incidental reply context.
    if len(parts) > 1 and valid_target(parts[1]):
        return parts[1], parts[2] if len(parts) > 2 else ""
    replied = message.reply_to_message
    if replied:
        if replied.sender_chat or not replied.from_user or replied.from_user.is_bot:
            raise DomainError("anonymous_identity")
        user = replied.from_user
        await service.observe(user.id, user.username, user.full_name)
        return str(user.id), " ".join(parts[1:])
    return "", ""


async def show_profile(
    message: Message, service: Service, target: str, *, edit: bool = False
) -> None:
    data = await service.profile(target)
    await render(
        message,
        p.profile(data),
        reply_markup=kb.check_result(data["user"].telegram_id),
        edit=edit,
    )


async def begin_report(message: Message, state: FSMContext, *, edit: bool = False) -> None:
    if message.chat.type != "private":
        await render(message, p.text("private"), edit=edit)
        return
    await clear_flow(state)
    await state.set_state(ReportFlow.target)
    nonce = uuid4().hex[:16]
    await state.update_data(nonce=nonce, evidence=[])
    if not edit:
        await flow_screen(
            message, state, p.text("report_target"), reply_markup=kb.report(nonce, "target")
        )
    else:
        result = await render(
            message, p.text("report_target"), reply_markup=kb.report(nonce, "target")
        )
        if isinstance(result, Message):
            await state.update_data(screen_message_id=result.message_id)


async def home_keyboard(service: Service, actor: int, *, private: bool) -> InlineKeyboardMarkup:
    return kb.home(
        admin=private and await service.is_admin(actor),
        owner=private and service.access.is_owner(actor),
        private=private,
    )


async def show_runtime_status(
    message: Message, state: FSMContext, service: Service, actor: int
) -> None:
    from app.health import snapshot
    from app.mtproto_relay import active_relay

    await service.require_admin(actor)
    if message.chat.type != "private":
        await message.answer(p.text("admin_private"))
        return
    data = snapshot()
    metrics = await service.repo.operational_stats()
    connected = active_relay()
    is_connected = bool(connected and getattr(connected.client, "is_connected", lambda: False)())
    relay = (
        "disabled"
        if not service.settings.group_help_enabled
        else "connected"
        if is_connected
        else "disconnected"
    )
    await clear_flow(state)
    await flow_screen(
        message,
        state,
        t(
            "diagnostic.status",
            ban_terminal=metrics["ban_terminal"],
            oldest_pending_seconds=metrics["oldest_pending_seconds"],
            pending_reports=metrics["pending_reports"],
            pending_rep=metrics["pending_rep"],
            health=t("diagnostic.healthy" if data["healthy"] else "diagnostic.unhealthy"),
            relay=t("diagnostic." + relay),
            **{
                key: data[key] if data[key] is not None else t("diagnostic.no_signal")
                for key in ("uptime", "poll", "worker")
            },
        ),
        reply_markup=kb.back("admin"),
    )


def create_router(settings: Any, session_factory: Any) -> Router:
    router = Router(name="safecheck")
    middleware = ServiceMiddleware(settings, session_factory)
    router.message.outer_middleware(middleware)
    router.callback_query.outer_middleware(middleware)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext, service: Service) -> None:
        await clear_flow(state)
        if await service.language(actor_id(message)) is None:
            await flow_screen(
                message, state, t("language.choose"), reply_markup=kb.languages(), home_photo=True
            )
            return
        await flow_screen(
            message,
            state,
            p.home(),
            reply_markup=await home_keyboard(
                service, actor_id(message), private=message.chat.type == "private"
            ),
            home_photo=True,
        )

    @router.message(Command("language"))
    async def choose_language(message: Message, state: FSMContext) -> None:
        await clear_flow(state)
        await flow_screen(message, state, t("language.choose"), reply_markup=kb.languages())

    @router.message(Command("help"))
    async def help_command(message: Message, state: FSMContext) -> None:
        await clear_flow(state)
        if message.chat.type in {"group", "supergroup"}:
            await flow_screen(message, state, p.info(), persistent=True)
        else:
            await flow_screen(message, state, t("core.help"), reply_markup=kb.result())

    @router.callback_query(Language.filter())
    async def select_language(
        callback: CallbackQuery, callback_data: Language, state: FSMContext, service: Service
    ) -> None:
        if callback_data.lang not in {"lt", "en", "ru"}:
            await callback.answer(t("language.invalid"), show_alert=True)
            return
        if not isinstance(callback.message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        await service.set_language(callback.from_user.id, callback_data.lang)
        language.set(callback_data.lang)  # outer middleware resets at update end
        await clear_flow(state)
        await callback.answer(t("language.changed"))
        await render(
            callback.message,
            t("language.changed") + "\n\n" + p.home(),
            reply_markup=await home_keyboard(
                service, callback.from_user.id, private=callback.message.chat.type == "private"
            ),
            home_photo=True,
        )
        if callback.message.chat.type == "private":
            assert callback.bot is not None
            await user_commands(
                callback.bot,
                callback.from_user.id,
                callback_data.lang,
                admin=await service.is_admin(callback.from_user.id),
            )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await close_panel(message, state)

    @router.message(Command("info"))
    async def redsafe_info(message: Message, service: Service, state: FSMContext) -> None:
        target, _ = await target_from_message(message, service)
        if not target:
            target = str(actor_id(message))
        await clear_flow(state)
        data = await rp.profile_data(service, message.bot, target)
        await flow_screen(
            message,
            state,
            rp.profile_text(data),
            reply_markup=rp.controls(data["user"].id),
            persistent=True,
        )

    @router.message(Command("ask", "rep", "profile"))
    async def check(message: Message, state: FSMContext, service: Service) -> None:
        command = message_content(message).split()[0].split("@")[0]
        persistent = command == "/ask"
        await clear_flow(state)
        target, _ = await target_from_message(message, service)
        if command == "/profile" and not target:
            target = str(actor_id(message))
        if target:
            data = await service.profile(target, refresh_identity=True)
            await flow_screen(
                message,
                state,
                p.profile(data),
                reply_markup=kb.check_result(data["user"].telegram_id, persistent=persistent),
                persistent=persistent,
            )
        else:
            await state.set_state(InputFlow.lookup)
            await state.update_data(lookup_persistent=persistent)
            await flow_screen(message, state, p.text("target"), reply_markup=kb.navigation())

    @router.message(Command("top"))
    async def top(message: Message, service: Service, state: FSMContext) -> None:
        await clear_flow(state)
        rows = await service.leaderboard()
        await flow_screen(message, state, p.leaderboard(rows), reply_markup=kb.leaderboard(rows))

    @router.message(Command("scammers"))
    async def scammers(message: Message, service: Service, state: FSMContext) -> None:
        if message.chat.type != "private":
            await flow_screen(message, state, p.text("scams_private"))
            return
        if await service.is_admin(actor_id(message)):
            await scam_listing(message, state, service)
            return
        await clear_flow(state)
        rows, total = await service.scams(0)
        await flow_screen(message, state, p.scams(rows), reply_markup=kb.pages(0, total))

    @router.message(Command("status"))
    async def runtime_status(message: Message, service: Service, state: FSMContext) -> None:
        await show_runtime_status(message, state, service, actor_id(message))

    @router.message(Command("report"))
    async def report_command(message: Message, state: FSMContext, service: Service) -> None:
        await begin_report(message, state)
        if message.chat.type != "private":
            return
        target, reason = await target_from_message(message, service)
        if target:
            user = await service.resolve(target)
            await state.update_data(
                target=f"@{user.username}" if user.username else p.display_name(user),
                target_identity=f"u:{user.id}",
            )
            if 10 <= len(reason.strip()) <= 1500:
                await state.update_data(reason=reason.strip())
                await state.set_state(ReportFlow.evidence)
                prompt = "evidence"
            else:
                await state.set_state(ReportFlow.reason)
                prompt = "reason"
            data = await state.get_data()
            await flow_screen(
                message, state, p.text(prompt), reply_markup=kb.report(data["nonce"], prompt)
            )

    @router.message(
        F.text.regexp(r"(?i)^[+-]rep(?:\s|$)") | F.caption.regexp(r"(?i)^[+-]rep(?:\s|$)")
    )
    async def vote(message: Message, state: FSMContext, service: Service) -> None:
        await clear_flow(state)
        target, comment = await target_from_message(message, service)
        if not target:
            await send_screen(message, p.text("rep_target"), None, False)
            return
        value = 1 if message_content(message).startswith("+") else -1
        if not comment:
            await begin_vote(message, state, service, actor_id(message), target, value)
            return
        data = await service.vote(
            actor_id(message),
            target,
            value,
            message.chat.id,
            f"msg:{message.chat.id}:{message.message_id}",
            comment=comment,
        )
        await flow_screen(
            message,
            state,
            p.reputation_pending(data),
            reply_markup=kb.back() if message.chat.type == "private" else None,
        )

    register_administrator_handlers(router)

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext, service: Service) -> None:
        if not await service.is_admin(actor_id(message)):
            await send_screen(message, p.text("denied"), None, False, persistent=True)
            return
        if message.chat.type != "private":
            await flow_screen(message, state, p.text("admin_private"))
            return
        await clear_flow(state)
        await flow_screen(
            message,
            state,
            p.text("admin"),
            reply_markup=kb.admin(owner=service.access.is_owner(actor_id(message))),
        )

    @router.message(Command("add_trusted", "del_trusted"))
    async def trusted_command(message: Message, state: FSMContext, service: Service) -> None:
        actor = actor_id(message)
        await service.require_admin(actor)
        target, extra = await target_from_message(message, service)
        if not target or extra.strip():
            await clear_flow(state)
            await flow_screen(message, state, p.text("trusted_usage"))
            return
        active = (message.text or "").split()[0].split("@")[0] == "/add_trusted"
        data = await service.set_trusted(
            actor, target, active, f"trusted:{message.chat.id}:{message.message_id}"
        )
        await clear_flow(state)
        key = (
            "trusted_replayed"
            if data.get("trusted_request_replayed")
            else "trusted_added"
            if active
            else "trusted_manual_removed_role"
            if data.get("trusted_source") == "role"
            else "trusted_manual_removed_top"
            if data["trusted"]
            else "trusted_removed"
        )
        await flow_screen(
            message,
            state,
            p.trusted_granted(data["user"])
            if key == "trusted_added"
            else t("p." + key, user=p.identity(data["user"])),
            persistent=True,
        )

    @router.message(Command("add_sc", "del_sc"))
    async def scam_command(message: Message, state: FSMContext, service: Service) -> None:
        if not await service.is_admin(actor_id(message)):
            await send_screen(message, p.text("denied"), None, False, persistent=True)
            return
        operation = (message.text or "").split()[0].split("@")[0][1:]
        await clear_flow(state)
        target, reason = await target_from_message(message, service)
        if target and operation == "del_sc" and not reason.strip():
            reason = "Administrator removal via /del_sc"
        if target and (operation == "add_sc" or 10 <= len(reason.strip()) <= 1500):
            user = await service.resolve(target)
            identity = f"u:{user.id}"
            if operation == "add_sc":
                record = await service.add_scam(actor_id(message), identity, reason)
                receipt = await registered_scam_text(
                    message.bot,
                    service,
                    session_factory,
                    record,
                    announce=True,
                    origin_chat_id=message.chat.id if message.chat.type != "private" else None,
                )
            else:
                removed = await service.remove_scam(actor_id(message), identity, reason)
                if not removed:
                    await flow_screen(
                        message,
                        state,
                        p.text("not_active"),
                        reply_markup=kb.back("admin") if message.chat.type == "private" else None,
                    )
                    return
                receipt = p.scam_action(user, False)
            await flow_screen(
                message,
                state,
                receipt,
                reply_markup=scam_controls(record, persistent=True)
                if operation == "add_sc" and message.chat.type == "private"
                else scam_refresh_controls(record)
                if operation == "add_sc"
                else kb.back("admin", "receipt")
                if message.chat.type == "private"
                else None,
                persistent=True,
            )
            if operation == "add_sc" and message.chat.type == "private":
                await scam_offer(message, state, record)
            return
        if message.chat.type != "private":
            await flow_screen(message, state, p.text("scam_group_usage"))
            return
        await state.set_state(InputFlow.admin_target)
        await state.update_data(operation=operation)
        await flow_screen(
            message, state, p.text("admin_target"), reply_markup=kb.navigation("admin")
        )

    @router.callback_query(Action.filter())
    async def action(
        callback: CallbackQuery, callback_data: Action, state: FSMContext, service: Service
    ) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        name, value = callback_data.name, callback_data.value
        actor = callback.from_user.id
        message = callback.message
        if name in ADMIN_ACTIONS and not await service.is_admin(actor):
            await callback.answer(p.text("denied"), show_alert=True)
            return
        if name in ADMIN_ACTIONS and message.chat.type != "private":
            await callback.answer(p.text("admin_private"), show_alert=True)
            return
        if (
            name == "profile"
            and value
            and (
                not value.isascii()
                or not value.isdigit()
                or not valid_target(value)
                or int(value) <= 0
            )
        ):
            await callback.answer(t("p.lookup_profile_unavailable"), show_alert=True)
            return
        await callback.answer()
        # The check card no longer has a home button to mark it as chat history.
        check_source = bool(
            message.reply_markup
            and any(
                button.callback_data
                and button.callback_data.startswith("sc|profile|")
                and button.callback_data != kb.action("profile")
                for row in message.reply_markup.inline_keyboard
                for button in row
            )
        )
        receipt = (check_source and name not in {"profile", "redsafe_check"}) or bool(
            message.reply_markup
            and name not in {"profile", "redsafe_check"}
            and any(
                button.callback_data == kb.action("home", "receipt")
                for row in message.reply_markup.inline_keyboard
                for button in row
            )
        )
        if receipt:
            preserved_source.set(message.message_id)
        if name in {"home", "admin", "pending"} and value == "receipt":
            preserved_source.set(message.message_id)
            if name == "pending":
                value = ""
        elif not receipt:
            await state.update_data(screen_message_id=message.message_id)
        if name == "language":
            await clear_flow(state)
            await render(message, t("language.choose"), reply_markup=kb.languages())
        elif name == "close":
            await close_panel(message, state, callback=True)
        elif name == "home":
            await clear_flow(state)
            await render(
                message,
                p.home(),
                reply_markup=await home_keyboard(
                    service, actor, private=message.chat.type == "private"
                ),
                home_photo=True,
            )
        elif name == "info":
            await clear_flow(state)
            await render(message, p.info(), reply_markup=kb.back())
        elif name in {"lookup", "rep"}:
            parent, parent_value = "home", ""
            if value:
                match = re.fullmatch(r"(scams|admin_scams):([0-9]{1,6})", value)
                if name != "lookup" or match is None:
                    await render(message, p.text("stale"))
                    return
                parent, parent_value = match.groups()
                if parent == "admin_scams":
                    await service.require_admin(actor)
                if message.chat.type != "private":
                    await render(message, p.text("scams_private"))
                    return
            await clear_flow(state)
            await state.set_state(InputFlow.lookup if name == "lookup" else InputFlow.reputation)
            await state.update_data(
                lookup_return_name=parent,
                lookup_return_value=parent_value,
                lookup_persistent=receipt and name == "lookup",
            )
            if name == "lookup" and message.chat.type in {"group", "supergroup"}:
                # A forced reply reaches the bot even when Telegram privacy mode is enabled.
                prompt = await message.answer(
                    callback.from_user.mention_html() + "\n" + p.text("target"),
                    reply_markup=ForceReply(selective=True),
                )
                await state.update_data(screen_message_id=prompt.message_id)
            else:
                await render(
                    message,
                    p.text("target" if name == "lookup" else "rep_target"),
                    reply_markup=kb.navigation(parent, parent_value),
                )
        elif name == "profile":
            await clear_flow(state)
            data = await rp.profile_data(service, message.bot, value or str(actor))
            await render(message, rp.profile_text(data), reply_markup=rp.controls(data["user"].id))
        elif name == "redsafe_check":
            await clear_flow(state)
            data = await service.profile(value)
            await render(
                message, p.profile(data), reply_markup=kb.check_result(data["user"].telegram_id)
            )
        elif name == "redsafe_names":
            await clear_flow(state)
            user = await service.resolve(value)
            await render(
                message,
                await rp.names_text(service, value),
                reply_markup=rp.names_controls(user),
            )
        elif name == "top":
            await clear_flow(state)
            top_rows = await service.leaderboard()
            await render(message, p.leaderboard(top_rows), reply_markup=kb.leaderboard(top_rows))
        elif name == "admin_scams":
            if not value.isascii() or not value.isdigit() or len(value) > 6:
                await render(message, p.text("stale"))
                return
            await scam_listing(message, state, service, int(value))
        elif name == "scams":
            if message.chat.type != "private":
                await render(message, p.text("scams_private"))
                return
            if not value.isascii() or not value.isdigit() or len(value) > 6:
                await render(message, p.text("stale"))
                return
            if await service.is_admin(actor):
                await scam_listing(message, state, service, int(value))
                return
            await clear_flow(state)
            page = int(value)
            rows, total = await service.scams(page)
            if page > max(0, (total - 1) // 5):
                page = max(0, (total - 1) // 5)
                rows, total = await service.scams(page)
            await render(
                message,
                p.scams(rows),
                reply_markup=kb.pages(page, total, admin=name == "admin_scams"),
            )
        elif name == "report":
            await begin_report(message, state, edit=True)
        elif name in {"vote+", "vote-"}:
            if not valid_target(value) and not re.fullmatch(r"u:[1-9][0-9]{0,18}", value):
                await render(message, p.text("stale"))
                return
            await begin_vote(message, state, service, actor, value, 1 if name == "vote+" else -1)
        elif name == "admin":
            await clear_flow(state)
            await render(
                message,
                p.text("admin"),
                reply_markup=kb.admin(owner=service.access.is_owner(actor)),
            )
        elif name == "status":
            await show_runtime_status(message, state, service, actor)
        elif name in {"add_sc", "del_sc"}:
            await clear_flow(state)
            await state.set_state(InputFlow.admin_target)
            await state.update_data(screen_message_id=message.message_id)
            await state.update_data(operation=name)
            await render(message, p.text("admin_target"), reply_markup=kb.navigation("admin"))
        elif name == "admin_target_back":
            draft = await state.get_data()
            if (
                await state.get_state() != InputFlow.admin_reason.state
                or draft.get("operation") != "del_sc"
            ):
                await render(message, p.text("stale"), reply_markup=kb.back("admin"))
                return
            await state.set_state(InputFlow.admin_target)
            await state.update_data(target="", target_identity="")
            await render(message, p.text("admin_target"), reply_markup=kb.navigation("admin"))
        elif name == "rep_admin":
            await clear_flow(state)
            await render(message, p.text("rep_admin"), reply_markup=kb.reputation_admin())
        elif name in {"rep_add", "rep_sub", "rep_reset", "top_include", "top_exclude"}:
            await clear_flow(state)
            nonce = uuid4().hex[:16]
            await state.update_data(operation=name, nonce=nonce)
            await state.set_state(AdminRepFlow.target)
            await state.update_data(screen_message_id=message.message_id)
            await render(
                message, p.text("admin_target"), reply_markup=kb.admin_rep_navigation(nonce, False)
            )
        elif name == "rep_pending":
            if not value.isascii() or not value.isdigit() or len(value) > 6 or int(value) > 100000:
                await render(
                    message,
                    p.text("stale"),
                    reply_markup=kb.admin(owner=service.access.is_owner(actor)),
                )
                return
            await clear_flow(state)
            page = int(value)
            rep_rows, total = await service.pending_reputation(actor, page)
            last = max(0, (total - 1) // service.PAGE_SIZE)
            if page > last:
                page = last
                rep_rows, total = await service.pending_reputation(actor, page)
            await state.update_data(rep_queue_page=page)
            await render(
                message,
                p.reputation_queue(rep_rows, total),
                reply_markup=kb.reputation_queue(rep_rows, page, total),
            )
        elif name == "rep_review":
            page = (await state.get_data()).get("rep_queue_page", 0)
            await clear_flow(state)
            await state.update_data(rep_queue_page=page)
            if not re.fullmatch(r"RP-[0-9]{4}-[0-9]{6,12}", value):
                await render(
                    message,
                    p.text("stale"),
                    reply_markup=kb.admin(owner=service.access.is_owner(actor)),
                )
                return
            request = await service.reputation_details(actor, value)
            await render(
                message,
                p.reputation_review(request),
                reply_markup=kb.reputation_review(value, page),
            )
        elif name == "pending":
            await clear_flow(state)
            if value and not re.fullmatch(r"SC-[0-9]{4}-[0-9]{6,12}", value):
                await render(message, p.text("stale"))
                return
            next_report = await service.pending_next(actor, value or None)
            if next_report:
                details = await service.report_details(actor, next_report.reference)
                await render(
                    message,
                    p.report_card(details),
                    reply_markup=kb.moderation(next_report.reference),
                )
            else:
                await render(
                    message,
                    p.text("pending_empty"),
                    reply_markup=kb.admin(owner=service.access.is_owner(actor)),
                )
        elif name == "trusted_admin":
            await open_trusted_panel(message, state, service, actor)
        elif name == "users":
            if not value.isascii() or not value.isdigit() or len(value) > 6:
                await render(message, p.text("stale"))
                return
            await clear_flow(state)
            page = int(value)
            user_rows, total = await service.users(actor, page)
            last = max(0, (total - 1) // service.USERS_PAGE_SIZE)
            if page > last:
                page = last
                user_rows, total = await service.users(actor, page)
            await render(
                message,
                p.users(user_rows, offset=page * service.USERS_PAGE_SIZE),
                reply_markup=kb.admin_pages(
                    "users", page, total, page_size=service.USERS_PAGE_SIZE
                ),
            )
        elif name == "stats":
            await clear_flow(state)
            await render(
                message,
                p.stats(await service.admin_stats(actor)),
                reply_markup=kb.back("admin"),
            )
        elif name == "audit":
            if value and (not value.isascii() or not value.isdigit() or len(value) > 6):
                await render(message, p.text("stale"))
                return
            await clear_flow(state)
            page = int(value or "0")
            audit_rows, total = await service.audit_page(actor, page)
            last = max(0, (total - 1) // service.PAGE_SIZE)
            if page > last:
                page = last
                audit_rows, total = await service.audit_page(actor, page)
            await render(
                message, p.audit(audit_rows), reply_markup=kb.admin_pages("audit", page, total)
            )
        elif name != "noop":
            await render(
                message,
                p.text("stale"),
                reply_markup=kb.back() if message.chat.type == "private" else None,
            )

    @router.callback_query(Moderation.filter())
    async def moderate(
        callback: CallbackQuery, callback_data: Moderation, state: FSMContext, service: Service
    ) -> None:
        if not await service.is_admin(callback.from_user.id):
            await callback.answer(p.text("denied"), show_alert=True)
            return
        if not isinstance(callback.message, Message) or not re.fullmatch(
            r"SC-[0-9]{4}-[0-9]{6,12}", callback_data.reference
        ):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        if callback.message.chat.type != "private":
            await callback.answer(p.text("admin_private"), show_alert=True)
            return
        await callback.answer()
        details = await service.report_details(callback.from_user.id, callback_data.reference)
        if callback_data.action == "evidence":
            if not details["evidence"]:
                await callback.message.answer(p.text("evidence_empty"))
            for evidence in details["evidence"]:
                if evidence.kind == "photo":
                    await callback.message.answer_photo(evidence.file_id)
                elif evidence.kind == "document":
                    await callback.message.answer_document(evidence.file_id)
                elif evidence.kind == "message":
                    from html import escape

                    await callback.message.answer(escape(evidence.file_id))
        elif callback_data.action in {"approve", "reject"}:
            await service.moderate(
                callback.from_user.id, callback_data.reference, callback_data.action == "approve"
            )
            receipt = p.moderation_action(
                details["target"], callback_data.reference, callback_data.action == "approve"
            )
            receipt_markup = kb.back("pending", "receipt")
            if callback_data.action == "approve":
                active = await service.repo.active_scam(details["target"].id)
                if active is not None:
                    record = await service.repo.scam_by_id(active.id)
                    if record is not None:
                        receipt = await registered_scam_text(
                            callback.bot, service, session_factory, record
                        )
                        receipt_markup.inline_keyboard.extend(
                            scam_refresh_controls(record, allow_remove=True).inline_keyboard
                        )
            await flow_screen(
                callback.message,
                state,
                receipt,
                reply_markup=receipt_markup,
                persistent=True,
            )
        else:
            await callback.message.answer(p.text("stale"))

    @router.callback_query(ReportStep.filter())
    async def report_step(
        callback: CallbackQuery, callback_data: ReportStep, state: FSMContext, service: Service
    ) -> None:
        if isinstance(callback.message, Message) and callback.message.chat.type != "private":
            await callback.answer(p.text("private"), show_alert=True)
            return
        data = await state.get_data()
        stage = await state.get_state()
        if (
            not isinstance(callback.message, Message)
            or data.get("nonce") != callback_data.nonce
            or stage
            not in {
                ReportFlow.target.state,
                ReportFlow.reason.state,
                ReportFlow.evidence.state,
                ReportFlow.preview.state,
            }
        ):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        await callback.answer()
        message = callback.message
        name = callback_data.action
        if name == "submit" and stage == ReportFlow.preview.state:
            report = await service.submit_report(
                callback.from_user.id,
                data["target_identity"],
                data["reason"],
                data.get("evidence", []),
                f"draft:{callback.from_user.id}:{data['nonce']}",
            )
            await clear_flow(state)
            import structlog

            structlog.get_logger().info(
                "report_submitted", operation="report_submit", report_reference=report.reference
            )
            await render(message, p.submitted(report.reference), reply_markup=kb.back())
        elif name == "preview" and stage == ReportFlow.evidence.state:
            await state.update_data(nonce=uuid4().hex[:16])
            data = await state.get_data()
            await state.set_state(ReportFlow.preview)
            await render(message, p.preview(data), reply_markup=kb.report(data["nonce"], "preview"))
        elif name == "edit" and stage == ReportFlow.preview.state:
            await state.set_state(ReportFlow.reason)
            await render(message, p.text("reason"), reply_markup=kb.report(data["nonce"], "reason"))
        elif name == "evidence" and stage == ReportFlow.preview.state:
            await state.set_state(ReportFlow.evidence)
            await render(
                message, p.text("evidence"), reply_markup=kb.report(data["nonce"], "evidence")
            )
        elif name == "back":
            previous = {
                ReportFlow.preview.state: (ReportFlow.evidence, "evidence"),
                ReportFlow.evidence.state: (ReportFlow.reason, "reason"),
                ReportFlow.reason.state: (ReportFlow.target, "target"),
            }.get(stage)
            if previous:
                await state.set_state(previous[0])
                await render(
                    message,
                    p.text("report_target" if previous[1] == "target" else previous[1]),
                    reply_markup=kb.report(data["nonce"], previous[1]),
                )
        else:
            await render(message, p.text("stale"))

    @router.message(InputFlow.lookup)
    @router.message(InputFlow.reputation)
    async def lookup_input(message: Message, state: FSMContext, service: Service) -> None:
        draft = await state.get_data()
        parent = draft.get("lookup_return_name", "home")
        parent_value = draft.get("lookup_return_value", "")
        persistent = bool(draft.get("lookup_persistent"))
        target = (message.text or "").strip()
        if not valid_target(target):
            await flow_screen(
                message, state, p.text("invalid"), reply_markup=kb.navigation(parent, parent_value)
            )
            return
        data = await service.profile(target, refresh_identity=True)
        await flow_screen(
            message,
            state,
            p.profile(data),
            reply_markup=kb.check_result(data["user"].telegram_id, persistent=persistent),
            persistent=persistent,
        )
        await clear_flow(state)

    @router.message(InputFlow.admin_target)
    async def admin_target(message: Message, state: FSMContext, service: Service) -> None:
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("admin_private"))
            return
        if not await service.is_admin(actor_id(message)):
            await clear_flow(state)
            await send_screen(message, p.text("denied"), None, False, persistent=True)
            return
        target = (message.text or "").strip()
        if not valid_target(target):
            await flow_screen(
                message, state, p.text("invalid"), reply_markup=kb.navigation("admin")
            )
            return
        user = await service.resolve(target)
        await state.update_data(
            target=f"@{user.username}" if user.username else p.display_name(user),
            target_identity=f"u:{user.id}",
        )
        data = await state.get_data()
        if data.get("operation") == "add_sc":
            record = await service.add_scam(actor_id(message), f"u:{user.id}")
            await clear_flow(state)
            await flow_screen(
                message,
                state,
                await registered_scam_text(
                    message.bot, service, session_factory, record, announce=True
                ),
                reply_markup=scam_controls(record, persistent=True),
                persistent=True,
            )
            await scam_offer(message, state, record)
            return
        await state.set_state(InputFlow.admin_reason)
        await flow_screen(
            message, state, p.text("admin_reason"), reply_markup=kb.navigation("admin_target_back")
        )

    @router.message(InputFlow.admin_reason)
    async def admin_reason(message: Message, state: FSMContext, service: Service) -> None:
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("admin_private"))
            return
        if not await service.is_admin(actor_id(message)):
            await clear_flow(state)
            await send_screen(message, p.text("denied"), None, False, persistent=True)
            return
        reason = (message.text or "").strip()
        if not 10 <= len(reason) <= 1500:
            await flow_screen(
                message,
                state,
                p.text("reason_invalid"),
                reply_markup=kb.navigation("admin_target_back"),
            )
            return
        data = await state.get_data()
        if data.get("operation") == "add_sc":
            record = await service.add_scam(actor_id(message), data["target_identity"], reason)
            receipt = await registered_scam_text(
                message.bot, service, session_factory, record, announce=True
            )
            key = "added"
        else:
            removed = await service.remove_scam(actor_id(message), data["target_identity"], reason)
            key = "removed" if removed else "not_active"
            receipt = (
                p.text(key)
                if key == "not_active"
                else p.scam_action(await service.resolve(data["target_identity"]), False)
            )
        await clear_flow(state)
        await flow_screen(
            message,
            state,
            receipt,
            reply_markup=scam_controls(record, persistent=True)
            if data.get("operation") == "add_sc"
            else kb.back("admin", "receipt"),
            persistent=True,
        )

    @router.message(ReportFlow.target)
    async def report_target(message: Message, state: FSMContext, service: Service) -> None:
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("private"))
            return
        target = (message.text or "").strip()
        if not valid_target(target):
            data = await state.get_data()
            await flow_screen(
                message, state, p.text("invalid"), reply_markup=kb.report(data["nonce"], "target")
            )
            return
        user = await service.resolve(target)
        current = await state.get_data()
        identity = f"u:{user.id}"
        if current.get("target_identity") and current["target_identity"] != identity:
            await state.update_data(reason="", evidence=[])
        await state.update_data(
            target=f"@{user.username}" if user.username else p.display_name(user),
            target_identity=identity,
        )
        await state.set_state(ReportFlow.reason)
        data = await state.get_data()
        await flow_screen(
            message, state, p.text("reason"), reply_markup=kb.report(data["nonce"], "reason")
        )

    @router.message(ReportFlow.reason)
    async def report_reason(message: Message, state: FSMContext) -> None:
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("private"))
            return
        reason = (message.text or "").strip()
        if not 10 <= len(reason) <= 1500:
            await flow_screen(
                message,
                state,
                p.text("reason_invalid"),
                reply_markup=kb.report((await state.get_data())["nonce"], "reason"),
            )
            return
        await state.update_data(reason=reason)
        await state.set_state(ReportFlow.evidence)
        data = await state.get_data()
        await flow_screen(
            message, state, p.text("evidence"), reply_markup=kb.report(data["nonce"], "evidence")
        )

    @router.message(ReportFlow.evidence)
    async def report_evidence(message: Message, state: FSMContext) -> None:
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("private"))
            return
        data = await state.get_data()
        evidence = data.get("evidence", [])
        if len(evidence) >= 10:
            await flow_screen(
                message,
                state,
                p.text("evidence_limit"),
                reply_markup=kb.report(data["nonce"], "evidence"),
            )
            return
        item = None
        if message.photo:
            item = {"kind": "photo", "file_id": message.photo[-1].file_id}
        elif message.document:
            item = {"kind": "document", "file_id": message.document.file_id}
        elif message.text and LINK.fullmatch(message.text.strip()):
            item = {"kind": "message", "file_id": message.text.strip()}
        if item is None:
            await flow_screen(
                message,
                state,
                p.text("evidence_invalid"),
                reply_markup=kb.report(data["nonce"], "evidence"),
            )
            return
        if item not in evidence:
            evidence.append(item)
        await state.update_data(evidence=evidence)
        await flow_screen(
            message,
            state,
            p.text("evidence") + f"\n\n📎 {len(evidence)} / 10",
            reply_markup=kb.report(data["nonce"], "evidence"),
        )

    register_scam_handlers(router)
    register_vote_handlers(router)
    register_trusted_handlers(router)
    register_reputation_handlers(router)
    register_group_handlers(router, settings, session_factory)

    @router.callback_query()
    async def invalid_callback(callback: CallbackQuery) -> None:
        await callback.answer(p.text("stale"), show_alert=True)

    @router.message()
    async def fallback(message: Message) -> None:
        return

    return router

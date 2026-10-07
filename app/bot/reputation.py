"""Administrator reputation review and adjustment Telegram flows."""

import re
from uuid import uuid4

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import presentation as p
from app.bot import keyboards as kb
from app.bot.callbacks import AdminRepStep, ReputationModeration
from app.bot.screens import clear_flow, flow_screen, render
from app.bot.states import AdminRepFlow
from app.bot.validation import actor_id, valid_target
from app.services import Service


def register_reputation_handlers(router: Router) -> None:
    @router.callback_query(ReputationModeration.filter())
    async def reputation_moderate(
        callback: CallbackQuery,
        callback_data: ReputationModeration,
        state: FSMContext,
        service: Service,
    ) -> None:
        await service.require_admin(callback.from_user.id)
        if not isinstance(callback.message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        if callback.message.chat.type != "private":
            await callback.answer(p.text("admin_private"), show_alert=True)
            return
        if callback_data.action not in {"approve", "reject"} or not re.fullmatch(
            r"RP-[0-9]{4}-[0-9]{6,12}", callback_data.reference
        ):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        await callback.answer()
        request = await service.moderate_reputation(
            callback.from_user.id, callback_data.reference, callback_data.action == "approve"
        )
        page = (await state.get_data()).get("rep_queue_page", 0)
        await clear_flow(state)
        await state.update_data(rep_queue_page=page)
        await render(
            callback.message,
            p.reputation_decision(request),
            reply_markup=kb.back("rep_pending", str(page)),
        )

    async def ensure_rep_admin(message: Message, state: FSMContext, service: Service) -> bool:
        await service.require_admin(actor_id(message))
        if message.chat.type != "private":
            await clear_flow(state)
            await flow_screen(message, state, p.text("admin_private"))
            return False
        return True

    @router.message(AdminRepFlow.target)
    async def admin_rep_target(message: Message, state: FSMContext, service: Service) -> None:
        if not await ensure_rep_admin(message, state, service):
            return
        data = await state.get_data()
        target = (message.text or "").strip()
        if not valid_target(target):
            await flow_screen(
                message,
                state,
                p.text("invalid"),
                reply_markup=kb.admin_rep_navigation(data["nonce"], False),
            )
            return
        user = await service.resolve(target)
        label = f"@{user.username}" if user.username else p.display_name(user)
        label += f" [{p.telegram_id_label(user)}]"
        await state.update_data(target_identity=f"u:{user.id}", target_label=label)
        amount = data["operation"] in {"rep_add", "rep_sub"}
        await state.set_state(AdminRepFlow.amount if amount else AdminRepFlow.reason)
        await flow_screen(
            message,
            state,
            p.text("rep_amount" if amount else "admin_reason"),
            reply_markup=kb.admin_rep_navigation(data["nonce"]),
        )

    @router.message(AdminRepFlow.amount)
    async def admin_rep_amount(message: Message, state: FSMContext, service: Service) -> None:
        if not await ensure_rep_admin(message, state, service):
            return
        data = await state.get_data()
        raw = (message.text or "").strip()
        if not raw.isascii() or not raw.isdigit() or len(raw) > 5 or not 1 <= int(raw) <= 10000:
            await flow_screen(
                message,
                state,
                p.text("rep_amount_invalid"),
                reply_markup=kb.admin_rep_navigation(data["nonce"]),
            )
            return
        await state.update_data(amount=int(raw))
        await state.set_state(AdminRepFlow.reason)
        await flow_screen(
            message,
            state,
            p.text("admin_reason"),
            reply_markup=kb.admin_rep_navigation(data["nonce"]),
        )

    @router.message(AdminRepFlow.reason)
    async def admin_rep_reason(message: Message, state: FSMContext, service: Service) -> None:
        if not await ensure_rep_admin(message, state, service):
            return
        reason = (message.text or "").strip()
        data = await state.get_data()
        if not 10 <= len(reason) <= 1500:
            await flow_screen(
                message,
                state,
                p.text("reason_invalid"),
                reply_markup=kb.admin_rep_navigation(data["nonce"]),
            )
            return
        await state.update_data(reason=reason, nonce=uuid4().hex[:16])
        await state.set_state(AdminRepFlow.preview)
        data = await state.get_data()
        await flow_screen(
            message,
            state,
            p.admin_reputation_preview(data),
            reply_markup=kb.admin_rep_preview(data["nonce"]),
        )

    @router.callback_query(AdminRepStep.filter())
    async def admin_rep_step(
        callback: CallbackQuery, callback_data: AdminRepStep, state: FSMContext, service: Service
    ) -> None:
        await service.require_admin(callback.from_user.id)
        if not isinstance(callback.message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        message = callback.message
        if message.chat.type != "private":
            await callback.answer(p.text("admin_private"), show_alert=True)
            return
        data = await state.get_data()
        stage = await state.get_state()
        if (
            not data.get("nonce")
            or data["nonce"] != callback_data.nonce
            or stage
            not in {
                AdminRepFlow.target.state,
                AdminRepFlow.amount.state,
                AdminRepFlow.reason.state,
                AdminRepFlow.preview.state,
            }
        ):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        await callback.answer()
        if callback_data.action == "back":
            if stage == AdminRepFlow.preview.state:
                previous, prompt = AdminRepFlow.reason, "admin_reason"
            elif stage == AdminRepFlow.reason.state and data["operation"] in {"rep_add", "rep_sub"}:
                previous, prompt = AdminRepFlow.amount, "rep_amount"
            elif stage in {AdminRepFlow.reason.state, AdminRepFlow.amount.state}:
                previous, prompt = AdminRepFlow.target, "admin_target"
            else:
                await clear_flow(state)
                await render(message, p.text("rep_admin"), reply_markup=kb.reputation_admin())
                return
            await state.set_state(previous)
            await render(
                message,
                p.text(prompt),
                reply_markup=kb.admin_rep_navigation(
                    data["nonce"], previous != AdminRepFlow.target
                ),
            )
            return
        if callback_data.action != "submit" or stage != AdminRepFlow.preview.state:
            await render(message, p.text("stale"), reply_markup=kb.reputation_admin())
            return
        actor, target, operation = callback.from_user.id, data["target_identity"], data["operation"]
        key = f"adminrep:{actor}:{data['nonce']}"
        if operation in {"rep_add", "rep_sub"}:
            value = data["amount"] if operation == "rep_add" else -data["amount"]
            result = await service.admin_adjust_rep(actor, target, value, data["reason"], key)
        elif operation == "rep_reset":
            result = await service.admin_reset_rep(actor, target, data["reason"], key)
        elif operation in {"top_include", "top_exclude"}:
            result = await service.set_top_visibility(
                actor, target, operation == "top_include", data["reason"], key
            )
        else:
            await render(message, p.text("stale"), reply_markup=kb.reputation_admin())
            return
        await clear_flow(state)
        title = "top_changed" if operation.startswith("top_") else "rep_changed"
        await render(
            message, p.text(title) + "\n\n" + p.profile(result), reply_markup=kb.back("rep_admin")
        )

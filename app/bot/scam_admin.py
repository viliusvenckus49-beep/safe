"""Private administrator registry and confirmed identity supplements."""

from html import escape
from typing import Any
from uuid import uuid4

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import presentation as p
from app.bot.callbacks import ScamAdmin
from app.bot.keyboards import keyboard, pages
from app.bot.scam_notices import registered_scam_text
from app.bot.screens import clear_flow, flow_screen, preserved_source
from app.bot.states import ScamAdminFlow
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t
from app.scam_management import ScamManagement
from app.services import Service


def cb(name: str, value: str = "") -> str:
    return ScamAdmin(action=name, value=value).pack()


def refresh_controls(record):
    return keyboard([[(t("sm.refresh"), cb("retry_receipt", str(record.id)))]])


def controls(record, page: int = 0, *, persistent: bool = False):
    suffix = "_receipt" if persistent else ""
    rows = []
    if record.target.telegram_id is None:
        rows.append([(t("sm.id"), cb("id" + suffix, str(record.id)))])
    if not record.target.username:
        rows.append([(t("sm.username"), cb("username" + suffix, str(record.id)))])
    if persistent or record.target.telegram_id is not None:
        rows.append(
            [(t("sm.refresh" if persistent else "sm.retry"), cb("retry" + suffix, str(record.id)))]
        )
    rows.append([(t("button.back"), cb("page" + suffix, str(page)))])
    return keyboard(rows)


async def offer(message: Message, state: FSMContext, record):
    """Keep the just-created unresolved entry available for a following numeric reply."""
    if record.target.telegram_id is None:
        await state.set_state(ScamAdminFlow.input)
        await state.update_data(scam_record=record.id, scam_field="id", scam_page=0)


async def listing(message: Message, state: FSMContext, service: Service, page: int = 0):
    await service.require_admin(message.chat.id)
    rows, total = await service.scams(page)
    page = min(page, max(0, (total - 1) // 5))
    rows, total = await service.scams(page)
    await clear_flow(state)
    await state.update_data(scam_page=page)
    text = (
        t("sm.title")
        + "\n\n"
        + ("\n".join(p.identity(r.target) for r in rows) or p.text("scams_empty"))
    )
    buttons = [
        [
            (
                f"@{r.target.username}" if r.target.username else p.telegram_id_label(r.target),
                cb("view", str(r.id)),
            )
        ]
        for r in rows
    ]
    buttons.extend(
        [(button.text, button.callback_data or "") for button in row]
        for row in pages(page, total, admin=True).inline_keyboard
    )
    await flow_screen(message, state, text, reply_markup=keyboard(buttons))


def register_scam_handlers(router: Router):
    @router.callback_query(ScamAdmin.filter())
    async def callback_handler(
        callback: CallbackQuery,
        callback_data: ScamAdmin,
        state: FSMContext,
        service: Service,
        session_factory: Any,
    ):
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        actor = callback.from_user.id
        if (
            message.chat.type != "private" and callback_data.action != "retry_receipt"
        ) or not await service.is_admin(actor):
            await callback.answer(p.text("denied"), show_alert=True)
            return
        if callback_data.action not in {"retry", "retry_receipt"}:
            await callback.answer()
        name, value = callback_data.action, callback_data.value
        if name in {"id_receipt", "username_receipt", "page_receipt", "retry_receipt"}:
            preserved_source.set(message.message_id)
            name = name.removesuffix("_receipt")
        draft = await state.get_data()
        management = ScamManagement(service)
        if name == "noop":
            return
        if name == "confirm":
            if await state.get_state() != ScamAdminFlow.preview.state or value != draft.get(
                "scam_nonce"
            ):
                await flow_screen(
                    message,
                    state,
                    p.text("stale"),
                    reply_markup=keyboard([[(t("button.back"), cb("page", "0"))]]),
                )
                return
            record = await management.supplement(
                actor, draft["scam_record"], draft["scam_field"], draft["scam_value"], value
            )
            await clear_flow(state)
            await flow_screen(
                message,
                state,
                await registered_scam_text(callback.bot, service, session_factory, record),
                reply_markup=controls(record, persistent=True),
                persistent=True,
            )
            return
        if (
            not value.isascii()
            or not value.isdigit()
            or len(value) > 19
            or int(value) > 9223372036854775807
        ):
            await flow_screen(message, state, p.text("stale"))
            return
        if name == "page":
            if len(value) > 6:
                await flow_screen(message, state, p.text("stale"))
                return
            await listing(message, state, service, int(value))
            return
        if name not in {"view", "id", "username", "retry"}:
            await flow_screen(message, state, p.text("stale"))
            return
        if name == "retry":
            try:
                record = await management.detail(actor, int(value))
                if record.target.telegram_id is None:
                    await callback.answer(t("sm.need_id"), show_alert=True)
                    return
                await GroupService(service.settings, service.session).retry_scam_bans(
                    actor, record.id
                )
            except DomainError as error:
                await callback.answer(
                    p.text("cooldown") if error.code == "cooldown" else p.text("stale"),
                    show_alert=True,
                )
                return
            await callback.answer()
            text = await registered_scam_text(callback.bot, service, session_factory, record)
            markup = (
                controls(record, persistent=True)
                if message.chat.type == "private"
                else refresh_controls(record)
            )
            try:
                await message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as error:
                if "message is not modified" not in error.message.lower():
                    raise
            return
        record = await management.detail(actor, int(value))
        page = draft.get("scam_page", 0)
        await clear_flow(state)
        await state.update_data(scam_page=page)
        if name == "view":
            await flow_screen(
                message, state, p.scams([record]), reply_markup=controls(record, page)
            )
        else:
            await state.set_state(ScamAdminFlow.input)
            await state.update_data(scam_record=record.id, scam_field=name)
            await flow_screen(
                message,
                state,
                t("sm.prompt_" + name),
                reply_markup=keyboard(
                    [
                        [
                            (t("button.back"), cb("view", value)),
                        ],
                        [
                            (t("button.cancel"), cb("page", str(page))),
                        ],
                    ]
                ),
            )

    @router.message(ScamAdminFlow.input)
    async def input_handler(message: Message, state: FSMContext, service: Service):
        if message.chat.type != "private" or not message.from_user:
            return
        actor = message.from_user.id
        await service.require_admin(actor)
        draft = await state.get_data()
        record = await ScamManagement(service).detail(actor, draft["scam_record"])
        value = ScamManagement.validate(draft["scam_field"], message.text or "")
        nonce = uuid4().hex
        await state.set_state(ScamAdminFlow.preview)
        await state.update_data(scam_value=value, scam_nonce=nonce)
        await flow_screen(
            message,
            state,
            t("sm.preview", user=p.identity(record.target), value=escape(value)),
            reply_markup=keyboard(
                [
                    [(t("sm.confirm"), cb("confirm", nonce))],
                    [(t("button.back"), cb(draft["scam_field"], str(record.id)))],
                    [(t("button.cancel"), cb("page", str(draft.get("scam_page", 0))))],
                ]
            ),
        )

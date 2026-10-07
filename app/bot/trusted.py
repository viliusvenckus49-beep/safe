"""Private administrator TRUSTED journey; all authority is checked server-side."""

from uuid import uuid4

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app import presentation as p
from app import trusted_presentation as tp
from app.bot import trusted_keyboards as kb
from app.bot.callbacks import TrustedAdmin
from app.bot.screens import clear_flow, flow_screen, preserved_source, render
from app.bot.states import TrustedAdminFlow
from app.i18n import t
from app.services import Service
from app.trusted_management import TrustedManagement


async def show_page(message: Message, state: FSMContext, core: Service, actor: int, page: int = 0):
    draft = await state.get_data()
    query = draft.get("trusted_query", "")
    rows, total, page = await TrustedManagement(core).page(actor, page, query)
    await state.set_state(None)
    await state.update_data(trusted_page=page, trusted_nonce=None, trusted_target=None)
    await flow_screen(
        message, state, tp.listing(rows, query), reply_markup=kb.page(rows, page, total, query)
    )


async def open_panel(message: Message, state: FSMContext, core: Service, actor: int):
    await core.require_admin(actor)
    await clear_flow(state)
    await state.update_data(trusted_query="")
    await show_page(message, state, core, actor)


def register_trusted_handlers(router: Router) -> None:
    @router.callback_query(TrustedAdmin.filter())
    async def trusted_callback(
        callback: CallbackQuery, callback_data: TrustedAdmin, state: FSMContext, service: Service
    ):
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer(p.text("stale"), show_alert=True)
            return
        if message.chat.type != "private" or not await service.is_admin(callback.from_user.id):
            await callback.answer(p.text("denied"), show_alert=True)
            return
        await callback.answer()
        actor, name, value = callback.from_user.id, callback_data.action, callback_data.value
        if name == "page_receipt":
            preserved_source.set(message.message_id)
            name = "page"
        draft = await state.get_data()
        page = int(draft.get("trusted_page", 0))
        management = TrustedManagement(service)
        if name == "noop":
            return
        if name in {"cancel", "clear"}:
            if name == "clear":
                await state.update_data(trusted_query="")
                page = 0
            await show_page(message, state, service, actor, page)
        elif name == "page":
            if not value.isascii() or not value.isdigit() or len(value) > 6:
                await render(message, p.text("stale"), reply_markup=kb.back(page))
                return
            await show_page(message, state, service, actor, int(value))
        elif name == "search":
            await state.set_state(TrustedAdminFlow.search)
            await flow_screen(message, state, t("tm.search_prompt"), reply_markup=kb.search(page))
        elif name in {"view", "remove"}:
            if (
                not value.isascii()
                or not value.isdigit()
                or not 0 < int(value) <= 9223372036854775807
            ):
                await render(message, p.text("stale"), reply_markup=kb.back(page))
                return
            data = await management.detail(actor, int(value))
            if name == "view":
                await state.set_state(None)
                await state.update_data(trusted_nonce=None, trusted_target=None)
                await flow_screen(
                    message, state, tp.detail(data), reply_markup=kb.detail(data, page)
                )
            elif data["removable"]:
                nonce = uuid4().hex
                await state.set_state(TrustedAdminFlow.preview)
                await state.update_data(trusted_nonce=nonce, trusted_target=int(value))
                await flow_screen(
                    message, state, tp.preview(data), reply_markup=kb.preview(nonce, int(value))
                )
            else:
                await state.set_state(None)
                await state.update_data(trusted_nonce=None, trusted_target=None)
                await flow_screen(
                    message, state, tp.detail(data), reply_markup=kb.detail(data, page)
                )
        elif name == "confirm":
            if (
                await state.get_state() != TrustedAdminFlow.preview.state
                or value != draft.get("trusted_nonce")
                or not draft.get("trusted_target")
            ):
                await render(message, p.text("stale"), reply_markup=kb.back(page))
                return
            data = await management.revoke(actor, draft["trusted_target"], value)
            await state.set_state(None)
            await state.update_data(trusted_nonce=None, trusted_target=None)
            text = t("tm.done")
            if data["role"]:
                text += "\n\n" + t("tm.role_warning")
            await flow_screen(
                message, state, text, reply_markup=kb.back(page, persistent=True), persistent=True
            )
        else:
            await render(message, p.text("stale"), reply_markup=kb.back(page))

    @router.message(TrustedAdminFlow.search)
    async def search_input(message: Message, state: FSMContext, service: Service):
        if message.chat.type != "private" or not message.from_user:
            return
        await service.require_admin(message.from_user.id)
        query = (message.text or "").strip()
        if not query or len(query) > 64:
            await flow_screen(message, state, p.text("invalid"), reply_markup=kb.search(0))
            return
        await state.update_data(trusted_query=query)
        await show_page(message, state, service, message.from_user.id)

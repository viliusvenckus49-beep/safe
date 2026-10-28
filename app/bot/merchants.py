"""Private merchant directory, owner forms and existing-admin catalog management."""

import time
from html import escape

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app import presentation as p
from app.bot import keyboards as base_kb
from app.bot import merchant_keyboards as kb
from app.bot import merchant_views as view
from app.bot import redsafe_profile as rp
from app.bot.callbacks import Merchant
from app.bot.merchant_limits import SearchLimiter
from app.bot.screens import clear_flow, render
from app.bot.states import MerchantFlow
from app.errors import DomainError
from app.i18n import language, t
from app.merchant_catalog import INPUT_TTL
from app.merchant_services import MerchantService


def error_text(error):
    return t("error." + error.code) if error.code.startswith("merchant_") else p.error(error.code)


async def panel(message, state, text, markup):
    result = await render(message, text, reply_markup=markup, interactive=True)
    anchor = result.message_id if isinstance(result, Message) else message.message_id
    await state.update_data(merchant_panel=anchor, screen_message_id=anchor)


async def prompt(message, state, mode, text, *, entry=0, version=0, admin=False):
    await state.set_state(MerchantFlow.input)
    await state.update_data(
        mc_input={
            "mode": mode,
            "entry": entry,
            "version": version,
            "admin": admin,
            "until": time.time() + INPUT_TTL,
        }
    )
    await panel(message, state, text, base_kb.keyboard([[kb.back(admin)]]))


async def show_list(message, state, cat, actor, limiter, *, page=0, admin=False, city=None):
    context = (await state.get_data()).get("mc_context", {})
    if city is not None:
        context = {"city": city, "query": "", "admin": admin}
    if context.get("admin", False) != admin:
        context = {"city": "", "query": "", "admin": admin}
    query = context.get("query", "")
    if query:
        await limiter.admit(state, actor)
    rows, total, page = await cat.listing(
        city_code=context.get("city", ""), query=query, page=page, admin=admin, actor=actor
    )
    context["page"] = page
    context["admin"] = admin
    await state.update_data(mc_context=context)
    text = view.listing(rows, total, page, context.get("city", ""), query, admin)
    markup = kb.listing(rows, total, page, admin=admin)
    if query and not total and not admin:
        text = (
            view.heading("search_title")
            + "\n\n"
            + t("mc.query", query=escape(query))
            + "\n\n<b>"
            + t("mc.not_found")
            + "</b>\n\n"
            + t("mc.no_results")
        )
        markup = base_kb.keyboard(
            [[kb.button("again", "search")], [kb.button("cities", "cities")], [kb.back()]]
        )
    await panel(message, state, text, markup)


async def show_draft(message, state, cat, actor, proposal_id, admin=False):
    proposal, entry = await cat.proposal(actor, proposal_id, admin=admin)
    await panel(message, state, view.proposal_card(proposal, entry), kb.draft(proposal, admin))


async def continue_draft(message, state, cat, actor, proposal_id, admin=False):
    proposal, entry = await cat.proposal(actor, proposal_id, admin=admin)
    if not proposal.city_code:
        await panel(
            message,
            state,
            view.heading("terms_title") + "\n\n" + t("mc.city_prompt"),
            kb.draft_cities(proposal, admin),
        )
    elif not proposal.name:
        await prompt(
            message,
            state,
            "name",
            t("mc.name_prompt"),
            entry=proposal.id,
            version=proposal.version,
            admin=admin,
        )
    elif not proposal.description:
        await prompt(
            message,
            state,
            "description",
            t("mc.description_prompt"),
            entry=proposal.id,
            version=proposal.version,
            admin=admin,
        )
    else:
        await show_draft(message, state, cat, actor, proposal.id, admin)


async def show_mine(message, state, cat, actor):
    entry = await cat.repo.mine(actor)
    if entry is None:
        raise DomainError("merchant_missing")
    proposal = await cat.repo.open_proposal(entry.id) or await cat.repo.latest_proposal(entry.id)
    blocked = await cat.core.repo.has_scam_identity(entry.owner)
    text = view.private_entry(entry, proposal)
    if blocked:
        text += "\n\n" + t("error.merchant_blocked")
    await panel(message, state, text, kb.mine(entry, proposal, blocked=blocked))


async def show_admin_entry(message, state, cat, actor, entry_id):
    await cat.core.require_admin(actor)
    entry = await cat.repo.entry(entry_id)
    if entry is None:
        raise DomainError("merchant_missing")
    proposal = await cat.repo.open_proposal(entry.id) or await cat.repo.latest_proposal(entry.id)
    text = view.private_entry(entry, proposal)
    if await cat.core.repo.has_scam_identity(entry.owner):
        text += "\n\n" + t("error.merchant_blocked")
    markup = kb.admin_entry(entry)
    if proposal:
        markup.inline_keyboard.insert(
            0, [base_kb.inline_button(t("mc.preview"), callback_data=kb.cb("a_draft", proposal.id))]
        )
    await panel(message, state, text, markup)


def ordinary_input(message):
    return bool(
        message.text and not message.text.lstrip().lower().startswith(("/", "+rep", "-rep"))
    )


def register_merchant_handlers(router: Router):
    limiter = SearchLimiter()

    @router.callback_query(Merchant.filter())
    async def directory(
        callback: CallbackQuery, callback_data: Merchant, state: FSMContext, service
    ):
        data = callback_data
        actor = callback.from_user.id
        message = callback.message
        if not isinstance(message, Message):
            await callback.answer(t("error.merchant_stale"), show_alert=True)
            return
        cat = MerchantService(service)
        admin = data.action.startswith("a_")
        route = data.action[2:] if admin else data.action
        try:
            if message.chat.type != "private" or message.chat.id != actor:
                raise DomainError("merchant_private")
            if admin:
                await service.require_admin(actor)
            context = await state.get_data()
            if route == "root":
                if not any(
                    b.callback_data == data.pack()
                    for row in (
                        message.reply_markup.inline_keyboard if message.reply_markup else []
                    )
                    for b in row
                ):
                    raise DomainError("merchant_stale")
            elif context.get("merchant_panel") != message.message_id:
                raise DomainError("merchant_stale")
            if route == "noop":
                await callback.answer()
                return
            await state.set_state(None)
            await state.update_data(mc_input=None)
            if route == "root":
                await clear_flow(state)
                if admin:
                    await panel(message, state, view.heading("admin"), kb.admin_root())
                else:
                    await cat.available()
                    settings = await cat.settings()
                    await panel(
                        message,
                        state,
                        view.root(settings),
                        kb.root(await cat.repo.mine(actor) is not None),
                    )
            elif route == "cities" and not admin:
                await state.update_data(mc_context={"city": "", "query": "", "admin": False})
                await panel(
                    message,
                    state,
                    view.heading("cities_title") + "\n\n" + t("mc.cities_intro"),
                    kb.cities(),
                )
            elif route == "list":
                if data.page < -1:
                    raise DomainError("merchant_invalid")
                return_page = (
                    ((await state.get_data()).get("mc_context") or {}).get("page", 0)
                    if data.page == -1
                    else data.page
                )
                await show_list(
                    message,
                    state,
                    cat,
                    actor,
                    limiter,
                    page=return_page,
                    admin=admin,
                    city=(data.value or "")
                    if data.value or (await state.get_data()).get("mc_context") is None
                    else None,
                )
            elif route == "search":
                await state.update_data(mc_context={"city": "", "query": "", "admin": admin})
                await prompt(
                    message,
                    state,
                    "search",
                    view.heading("search_title") + "\n\n" + t("mc.search_prompt"),
                    admin=admin,
                )
            elif route == "info" and not admin:
                await panel(
                    message,
                    state,
                    view.heading("info") + "\n\n" + t("mc.info_body"),
                    base_kb.keyboard([[kb.back()]]),
                )
            elif route == "profile" and not admin:
                entry, stats = await cat.profile(data.entry)
                await panel(message, state, view.profile(entry, stats), kb.profile(entry))
            elif route == "reviews" and not admin:
                entry, rows, total, page = await cat.reviews(
                    data.entry, data.value or "all", data.page
                )
                stats = await service.repo.rep_stats(entry.owner_user_id)
                settings = await cat.settings()
                await panel(
                    message,
                    state,
                    view.reviews(
                        entry,
                        dict(zip(("score", "positive", "negative"), stats, strict=True)),
                        rows,
                        settings.publish_authors,
                    ),
                    kb.reviews(entry, page, total, data.value or "all"),
                )
            elif route in ("check", "activity") and not admin:
                entry = await cat.public(data.entry)
                target = str(entry.owner.telegram_id)
                await callback.answer()
                text = (
                    p.profile(await service.profile(target))
                    if route == "check"
                    else rp.profile_text(await rp.profile_data(service, callback.bot, target))
                )
                await service.session.commit()
                await panel(
                    message,
                    state,
                    text,
                    base_kb.keyboard(
                        [
                            [kb.button("profile_back", "profile", entry.id)],
                            [kb.button("list_back", "list", page=-1)],
                        ]
                    ),
                )
                return
            elif route == "mine" and not admin:
                await show_mine(message, state, cat, actor)
            elif route == "terms":
                await panel(
                    message,
                    state,
                    view.heading("terms_title") + "\n\n" + t("mc.terms"),
                    kb.terms(admin),
                )
            elif route == "target" and admin:
                await prompt(message, state, "target", t("mc.target_prompt"), admin=True)
            elif route == "begin" and not admin:
                proposal = await cat.begin(actor, consent=True)
                await continue_draft(message, state, cat, actor, proposal.id)
            elif route == "edit":
                entry = await (cat.repo.entry(data.entry) if admin else cat.repo.mine(actor))
                if entry is None or entry.id != data.entry:
                    raise DomainError("forbidden")
                proposal = await cat.begin(
                    actor,
                    "EDIT",
                    entry_id=entry.id,
                    target_id=entry.owner.telegram_id if admin else None,
                )
                await show_draft(message, state, cat, actor, proposal.id, admin)
            elif route == "draft":
                await show_draft(message, state, cat, actor, data.entry, admin)
            elif route in ("name", "description", "city"):
                proposal, entry = await cat.proposal(actor, data.entry, admin=admin)
                if proposal.version != data.version or proposal.status != "DRAFT":
                    raise DomainError("merchant_stale")
                if route == "city":
                    await panel(
                        message, state, t("mc.city_prompt"), kb.draft_cities(proposal, admin)
                    )
                else:
                    await prompt(
                        message,
                        state,
                        route,
                        t("mc." + route + "_prompt"),
                        entry=proposal.id,
                        version=proposal.version,
                        admin=admin,
                    )
            elif route == "setcity":
                proposal = await cat.update_draft(
                    actor, data.entry, "city_code", data.value, data.version, admin=admin
                )
                await continue_draft(message, state, cat, actor, proposal.id, admin)
            elif route == "submit":
                proposal = await cat.submit(actor, data.entry, data.version, admin=admin)
                await show_draft(message, state, cat, actor, proposal.id, admin)
            elif route == "cancel" and not admin:
                await cat.cancel(actor, data.entry, data.version)
                await show_mine(message, state, cat, actor)
            elif route == "removal" and not admin:
                entry = await cat.repo.mine(actor)
                if entry is None or entry.id != data.entry:
                    raise DomainError("forbidden")
                await panel(
                    message,
                    state,
                    t("mc.remove_confirm"),
                    base_kb.keyboard(
                        [
                            [kb.button("confirm", "requestremove", entry.id)],
                            [kb.button("back_mine", "mine")],
                        ]
                    ),
                )
            elif route == "requestremove" and not admin:
                proposal = await cat.begin(actor, "REMOVE", entry_id=data.entry)
                await cat.submit(actor, proposal.id, proposal.version)
                await show_mine(message, state, cat, actor)
            elif route == "queue" and admin:
                rows, total, page = await cat.pending(actor, data.page)
                buttons = [
                    [(f"#{row.id} · {t('mc.' + row.kind)}", kb.cb("a_draft", row.id))]
                    for row in rows
                ]
                buttons.extend([kb.pages("a_queue", page, total, 6), [kb.back(True)]])
                await panel(
                    message,
                    state,
                    view.heading("queue") + "\n\n" + (str(total) if rows else t("mc.empty")),
                    base_kb.keyboard(buttons),
                )
            elif route == "entry" and admin:
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "approve" and admin:
                proposal = await cat.moderate(actor, data.entry, data.version, True)
                await show_admin_entry(message, state, cat, actor, proposal.entry_id)
            elif route in ("reject", "suspend", "remove") and admin:
                await prompt(
                    message,
                    state,
                    route,
                    t("mc.reason_prompt"),
                    entry=data.entry,
                    version=data.version,
                    admin=True,
                )
            elif route == "restore" and admin:
                await cat.change_status(actor, data.entry, data.version, "APPROVED")
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "settings" and admin:
                row = await cat.settings()
                await panel(message, state, view.heading("settings"), kb.settings(row))
            elif route == "toggle" and admin:
                if data.value not in ("enabled", "registrations", "publish_authors"):
                    raise DomainError("merchant_invalid")
                row = await cat.settings()
                row = await cat.configure(
                    actor, data.version, data.value, not getattr(row, data.value)
                )
                await panel(message, state, view.heading("settings"), kb.settings(row))
            elif route == "copy" and admin:
                if data.value not in ("title", "intro"):
                    raise DomainError("merchant_invalid")
                await prompt(
                    message,
                    state,
                    "copy_" + data.value,
                    t("mc." + data.value + "_prompt"),
                    version=data.version,
                    admin=True,
                )
            elif route == "audit" and admin:
                rows, total, page = await cat.history(actor, data.entry, data.page)
                text = view.heading("audit")
                for row in rows:
                    text += f"\n\n{row.id} · {utc_date(row.created_at)}\n<code>{t('mc.audit_' + row.action.removeprefix('merchant_'))}</code> · <code>{row.actor_id}</code> · #{row.details.get('merchant_id') or '—'}"
                if not rows:
                    text += "\n\n" + t("mc.empty")
                await panel(
                    message,
                    state,
                    text,
                    base_kb.keyboard(
                        [kb.pages("a_audit", page, total, 5, entry=data.entry), [kb.back(True)]]
                    ),
                )
            else:
                raise DomainError("merchant_stale")
            await callback.answer()
        except DomainError as error:
            await service.session.rollback()
            await callback.answer(error_text(error), show_alert=True)
        except (SQLAlchemyError, RedisError) as error:
            await service.session.rollback()
            structlog.get_logger().warning(
                "merchant_storage_unavailable", exception_type=type(error).__name__
            )
            await callback.answer(t("p.error"), show_alert=True)

    @router.message(MerchantFlow.input, F.text, ordinary_input)
    async def input_text(message: Message, state: FSMContext, service):
        if message.from_user is None or message.text is None:
            return
        cat = MerchantService(service)
        actor = message.from_user.id
        context = await state.get_data()
        draft = context.get("mc_input") or {}
        anchor = context.get("merchant_panel")
        if message.chat.type != "private" or message.chat.id != actor:
            return
        if not anchor:
            return
        target = message.model_copy(update={"message_id": anchor})
        admin = bool(draft.get("admin"))
        mode = draft.get("mode")
        try:
            if time.time() > draft.get("until", 0):
                raise DomainError("merchant_expired")
            if admin:
                await service.require_admin(actor)
            if mode == "search":
                from app.merchant_catalog import search

                query = search(message.text)
                await state.update_data(mc_context={"city": "", "query": query, "admin": admin})
                await show_list(target, state, cat, actor, limiter, admin=admin)
            elif mode in ("name", "description"):
                proposal = await cat.update_draft(
                    actor, draft["entry"], mode, message.text, draft["version"], admin=admin
                )
                await continue_draft(target, state, cat, actor, proposal.id, admin)
            elif mode == "target" and admin:
                text = message.text.strip()
                if (
                    not text.isascii()
                    or not text.isdigit()
                    or not 0 < int(text) <= 9223372036854775807
                ):
                    raise DomainError("merchant_identity")
                proposal = await cat.begin(actor, target_id=int(text), consent=True)
                await continue_draft(target, state, cat, actor, proposal.id, True)
            elif mode == "reject" and admin:
                proposal = await cat.moderate(
                    actor, draft["entry"], draft["version"], False, message.text
                )
                await show_admin_entry(target, state, cat, actor, proposal.entry_id)
            elif mode in ("suspend", "remove") and admin:
                await cat.change_status(
                    actor,
                    draft["entry"],
                    draft["version"],
                    "SUSPENDED" if mode == "suspend" else "REMOVED",
                    message.text,
                )
                await show_admin_entry(target, state, cat, actor, draft["entry"])
            elif mode in ("copy_title", "copy_intro") and admin:
                row = await cat.configure(
                    actor, draft["version"], mode[5:], message.text, lang=language.get()
                )
                await panel(target, state, view.heading("settings"), kb.settings(row))
            else:
                raise DomainError("merchant_stale")
            # A guided form may have advanced to another prompt. Keep that newer state.
            current = (await state.get_data()).get("mc_input")
            if current == draft:
                await state.set_state(None)
                await state.update_data(mc_input=None)
            if message.message_id != anchor:
                try:
                    await message.delete()
                except TelegramBadRequest:
                    pass
        except DomainError as error:
            await service.session.rollback()
            if error.code in ("merchant_expired", "merchant_stale", "forbidden"):
                await state.set_state(None)
                await state.update_data(mc_input=None)
            await panel(target, state, error_text(error), base_kb.keyboard([[kb.back(admin)]]))
        except (SQLAlchemyError, RedisError) as error:
            await service.session.rollback()
            structlog.get_logger().warning(
                "merchant_storage_unavailable", exception_type=type(error).__name__
            )
            await panel(target, state, t("p.error"), base_kb.keyboard([[kb.back(admin)]]))


def utc_date(value):
    from app.merchant_catalog import utc

    return utc(value).strftime("%d.%m.%Y %H:%M")

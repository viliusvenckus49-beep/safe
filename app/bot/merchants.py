"""Private directory and direct administration; no application workflows."""

import secrets
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
from app.merchant_catalog import DRAFT_TTL, INPUT_TTL
from app.merchant_services import MerchantService


def error_text(error):
    return t("error." + error.code) if error.code.startswith("merchant_") else p.error(error.code)


async def panel(message, state, text, markup):
    result = await render(message, text, reply_markup=markup, interactive=True)
    anchor = result.message_id if isinstance(result, Message) else message.message_id
    await state.update_data(merchant_panel=anchor, screen_message_id=anchor)


async def prompt(message, state, mode, text, *, entry=0, version=0, admin=False, markup=None):
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
    await panel(message, state, text, markup or base_kb.keyboard([[kb.back(admin)]]))


async def show_list(message, state, cat, actor, limiter, *, page=0, admin=False, city=None):
    context = (await state.get_data()).get("mc_context", {})
    if city is not None:
        context = {"city": city, "query": "", "admin": admin}
    if context.get("admin", False) != admin:
        context = {"city": "", "query": "", "admin": admin}
    query = context.get("query", "") if admin else ""
    context["query"] = query
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
    await panel(message, state, text, markup)


async def show_editor(message, state):
    editor = (await state.get_data()).get("mc_editor")
    if not editor or time.time() > editor["until"]:
        raise DomainError("merchant_expired")
    await state.set_state(None)
    await state.update_data(mc_input=None)
    await panel(
        message,
        state,
        view.editor_card(editor["values"], editor["default_contact"]),
        kb.editor(editor),
    )


async def editor_session(state, actor, entry, revision, *, allow_expired=False):
    editor = (await state.get_data()).get("mc_editor") or {}
    if (
        not editor
        or editor.get("actor") != actor
        or editor.get("entry") != entry
        or editor.get("revision") != revision
    ):
        raise DomainError("merchant_stale")
    if not allow_expired and time.time() > editor["until"]:
        raise DomainError("merchant_expired")
    return editor


async def start_editor(message, state, actor, *, entry=None, target_id=0, owner=None):
    owner = entry.owner if entry else owner
    contact = ("@" + owner.username if owner.username else str(owner.telegram_id)) if owner else ""
    await state.update_data(
        mc_editor={
            "actor": actor,
            "entry": entry.id if entry else 0,
            "default_contact": contact,
            "base_version": entry.version if entry else 0,
            "target_id": target_id,
            "revision": secrets.randbelow(1000000000) + 1,
            "until": time.time() + DRAFT_TTL,
            "values": {
                field: getattr(entry, field, None)
                for field in ("name", "description", "city_code", "contact")
            },
        }
    )
    await show_editor(message, state)


async def show_mine(message, state, cat, actor):
    entry = await cat.repo.mine(actor)
    if entry is None:
        raise DomainError("merchant_missing")
    blocked = await cat.core.repo.has_scam_identity(entry.owner)
    text = view.private_entry(entry)
    if blocked:
        text += "\n\n" + t("error.merchant_blocked")
    await panel(message, state, text, kb.mine(entry, blocked=blocked))


async def show_admin_entry(message, state, cat, actor, entry_id):
    entry = await cat.editable(actor, entry_id)
    text = view.private_entry(entry, admin=True)
    if await cat.core.repo.has_scam_identity(entry.owner):
        text += "\n\n" + t("error.merchant_blocked")
    await panel(message, state, text, kb.admin_entry(entry))


def ordinary_input(message):
    if not message.text:
        return False
    text = message.text.lstrip()
    return not text.startswith("/") and text.lower().split(maxsplit=1)[:1] not in (
        ["+rep"],
        ["-rep"],
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
            if not (
                0 <= data.entry <= 2147483647
                and -1 <= data.page <= 2147483647
                and 0 <= data.version <= 2147483647
            ):
                raise DomainError("merchant_invalid")
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
                    await state.update_data(mc_context=context.get("mc_context"))
                    await panel(message, state, view.heading("admin"), kb.admin_root())
                else:
                    settings = await cat.settings()
                    if not settings.enabled:
                        await panel(
                            message,
                            state,
                            t("error.merchant_disabled"),
                            base_kb.keyboard([[(t("mc.home"), base_kb.action("home"))]]),
                        )
                        await callback.answer()
                        return
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
                if not admin:
                    raise DomainError("merchant_stale")
                await state.update_data(mc_context={"city": "", "query": "", "admin": admin})
                await prompt(
                    message,
                    state,
                    "search",
                    view.heading("search_title") + "\n\n" + t("mc.search_prompt"),
                    admin=admin,
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
            elif route == "entry" and admin:
                await state.update_data(mc_editor=None, mc_delete=None)
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "create" and admin:
                await state.update_data(mc_editor=None)
                await prompt(message, state, "target", t("mc.target_prompt"), admin=True)
            elif route == "editor" and admin:
                entry = await cat.editable(actor, data.entry)
                if entry.version != data.version or entry.status == "REMOVED":
                    raise DomainError("merchant_stale")
                await start_editor(message, state, actor, entry=entry)
            elif route in ("field", "cityvalue", "editback", "commit", "discard") and admin:
                editor = await editor_session(
                    state, actor, data.entry, data.version, allow_expired=route == "discard"
                )
                if route == "discard":
                    await state.update_data(mc_editor=None)
                    if editor["entry"]:
                        await show_admin_entry(message, state, cat, actor, editor["entry"])
                    else:
                        await panel(message, state, view.heading("admin"), kb.admin_root())
                elif route == "commit":
                    entry = await cat.save(
                        actor,
                        editor["entry"],
                        editor["base_version"],
                        editor["values"],
                        target_id=editor["target_id"],
                    )
                    await state.update_data(mc_editor=None)
                    await show_admin_entry(message, state, cat, actor, entry.id)
                elif route == "editback":
                    await show_editor(message, state)
                elif route == "cityvalue":
                    editor["values"]["city_code"] = cat.validate_field("city_code", data.value)
                    editor["revision"] += 1
                    await state.update_data(mc_editor=editor)
                    await show_editor(message, state)
                else:
                    field = data.value
                    if field not in ("name", "description", "city_code", "contact"):
                        raise DomainError("merchant_invalid")
                    text = view.field_prompt(field, editor["values"], editor["default_contact"])
                    if field == "city_code":
                        await panel(message, state, text, kb.editor_cities(editor))
                    else:
                        await prompt(
                            message,
                            state,
                            "field_" + field,
                            text,
                            entry=editor["entry"],
                            version=editor["revision"],
                            admin=True,
                            markup=base_kb.keyboard([[kb.field_back(editor)]]),
                        )
            elif route == "suspend" and admin:
                await cat.change_status(actor, data.entry, data.version, "SUSPENDED")
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "restore" and admin:
                await cat.change_status(actor, data.entry, data.version, "APPROVED")
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "remove" and admin:
                entry = await cat.editable(actor, data.entry)
                if entry.version != data.version or entry.status == "REMOVED":
                    raise DomainError("merchant_stale")
                token = secrets.token_hex(6)
                await state.update_data(
                    mc_delete={
                        "entry": entry.id,
                        "version": entry.version,
                        "token": token,
                        "until": time.time() + INPUT_TTL,
                    }
                )
                await panel(
                    message,
                    state,
                    t("mc.delete_confirm", name=escape(entry.name or entry.owner.display_name)),
                    base_kb.keyboard(
                        [
                            [
                                kb.button(
                                    "confirm",
                                    "a_delete",
                                    entry.id,
                                    version=entry.version,
                                    value=token,
                                )
                            ],
                            [kb.button("cancel_edit", "a_entry", entry.id)],
                        ]
                    ),
                )
            elif route == "delete" and admin:
                confirm = context.get("mc_delete") or {}
                if (
                    confirm.get("entry") != data.entry
                    or confirm.get("version") != data.version
                    or confirm.get("token") != data.value
                    or time.time() > confirm.get("until", 0)
                ):
                    raise DomainError("merchant_stale")
                await cat.change_status(actor, data.entry, data.version, "REMOVED")
                await state.update_data(mc_delete=None, mc_editor=None)
                await show_admin_entry(message, state, cat, actor, data.entry)
            elif route == "settings" and admin:
                row = await cat.settings()
                await panel(message, state, view.settings_card(row), kb.settings(row))
            elif route == "toggle" and admin:
                if data.value not in ("enabled", "publish_authors"):
                    raise DomainError("merchant_invalid")
                row = await cat.settings()
                row = await cat.configure(
                    actor, data.version, data.value, not getattr(row, data.value)
                )
                await panel(message, state, view.settings_card(row), kb.settings(row))
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
            if not admin:
                raise DomainError("merchant_stale")
            if mode == "search":
                if not admin:
                    raise DomainError("merchant_stale")
                from app.merchant_catalog import search

                query = message.text.strip()
                search(query)
                await state.update_data(mc_context={"city": "", "query": query, "admin": admin})
                await show_list(target, state, cat, actor, limiter, admin=admin)
            elif mode and mode.startswith("field_") and admin:
                editor = await editor_session(state, actor, draft["entry"], draft["version"])
                field = mode[6:]
                editor["values"][field] = cat.validate_field(field, message.text)
                editor["revision"] += 1
                await state.update_data(mc_editor=editor)
                await show_editor(target, state)
            elif mode == "target" and admin:
                text = message.text.strip()
                if not text.isascii() or not text.isdigit():
                    raise DomainError("merchant_identity")
                owner = await cat.creation_target(actor, int(text))
                await start_editor(target, state, actor, target_id=int(text), owner=owner)
            elif mode in ("copy_title", "copy_intro") and admin:
                row = await cat.configure(
                    actor, draft["version"], mode[5:], message.text, lang=language.get()
                )
                await panel(target, state, view.settings_card(row), kb.settings(row))
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
            if error.code in (
                "merchant_expired",
                "merchant_stale",
                "forbidden",
            ):
                await state.set_state(None)
                await state.update_data(mc_input=None)
            editor = (await state.get_data()).get("mc_editor")
            if error.code in ("merchant_expired", "forbidden"):
                await state.update_data(mc_editor=None)
                editor = None
            markup = (
                base_kb.keyboard([[kb.field_back(editor)]])
                if editor and admin
                else base_kb.keyboard([[kb.back(admin)]])
            )
            if error.code == "forbidden":
                markup = base_kb.keyboard([[(t("mc.home"), base_kb.action("home"))]])
            await panel(target, state, error_text(error), markup)
        except (SQLAlchemyError, RedisError) as error:
            await service.session.rollback()
            structlog.get_logger().warning(
                "merchant_storage_unavailable", exception_type=type(error).__name__
            )
            await panel(target, state, t("p.error"), base_kb.keyboard([[kb.back(admin)]]))

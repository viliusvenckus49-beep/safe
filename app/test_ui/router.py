"""Telegram editing journeys; production handlers are never registered here."""

import re
from html import escape, unescape
from importlib.resources import files

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, FSInputFile, Message

from app.bot import keyboards as kb
from app.i18n import LANGUAGES
from app.presentation import display_text
from app.test_ui.config import UISettings
from app.test_ui.preview import SCREENS, sample, screen
from app.test_ui.profile import BUTTON_KEYS, KEYS, MENUS, Design, fields, identifiers, units
from app.test_ui.texts import text

PAGE_SIZE = 12


class Edit(CallbackData, prefix="ui"):
    action: str
    value: str = ""


class Input(StatesGroup):
    text = State()
    search = State()
    layout = State()


def cb(action: str, value: str = "") -> str:
    return Edit(action=action, value=value).pack()


def short_label(value: str) -> str:
    lines = unescape(re.sub(r"<[^>]*>", "", value)).splitlines()
    useful = [
        line.strip()
        for line in lines
        if line.strip() and "𝑪𝑹𝑰𝑴𝑺𝑶𝑵" not in line and set(line.strip()) - set("━─")
    ]
    return display_text(" · ".join(useful) or value, 54)


class PrivateAdministrators(BaseMiddleware):
    def __init__(self, settings: UISettings):
        self.admins = settings.admins

    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        actor = event.from_user
        if (
            not isinstance(message, Message)
            or message.chat.type != "private"
            or actor is None
            or actor.id not in self.admins
        ):
            if isinstance(event, CallbackQuery):
                await event.answer(text("denied"), show_alert=True)
            elif isinstance(message, Message) and message.chat.type == "private":
                await message.answer(text("denied"))
            return None
        return await handler(event, data)


def create_router(settings: UISettings, design: Design) -> Router:
    router = Router(name="test-ui-only")
    guard = PrivateAdministrators(settings)
    router.message.outer_middleware(guard)
    router.callback_query.outer_middleware(guard)

    async def language(state: FSMContext) -> str:
        return (await state.get_data()).get("lang", "lt")

    def back(lang: str):
        return kb.keyboard([[(text("editor", lang), cb("home"))]])

    async def home(message: Message, state: FSMContext):
        await state.set_state(None)
        lang = await language(state)
        await message.answer(
            text("title", lang) + "\n\n" + text("intro", lang) + f"\n\n{lang.upper()}",
            reply_markup=kb.keyboard(
                [
                    [
                        (text("buttons", lang), cb("list", "buttons.0")),
                        (text("texts", lang), cb("list", "texts.0")),
                    ],
                    [(text("order", lang), cb("menus"))],
                    [(text("preview", lang), cb("previews"))],
                    [(text("language", lang), cb("languages"))],
                    [(text("export", lang), cb("export"))],
                ]
            ),
        )

    async def listing(message: Message, state: FSMContext, kind: str, page: int):
        if kind not in {"buttons", "texts"} or not 0 <= page <= 1000:
            raise ValueError("invalid")
        await state.set_state(None)
        lang = await language(state)
        query = (await state.get_data()).get("query", "").casefold()
        options = [
            key
            for key in KEYS
            if (key in BUTTON_KEYS) == (kind == "buttons")
            and (not query or query in design.text(lang, key).casefold() or query in key)
        ]
        last = max(0, (len(options) - 1) // PAGE_SIZE)
        page = min(page, last)
        await state.update_data(kind=kind, page=page)
        rows = [
            [(short_label(design.text(lang, key)), cb("key", str(KEYS.index(key))))]
            for key in options[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        ]
        navigation = []
        if page:
            navigation.append(("‹", cb("list", f"{kind}.{page - 1}")))
        if page < last:
            navigation.append(("›", cb("list", f"{kind}.{page + 1}")))
        if navigation:
            rows.append(navigation)
        rows.extend(
            [[(text("search", lang), cb("search", kind))], [(text("back", lang), cb("home"))]]
        )
        caption = text(kind, lang) + f" · {lang.upper()} · {page + 1}/{last + 1}"
        if not options:
            caption += "\n\n" + text("empty", lang)
        if query:
            caption += "\n\n🔍 " + escape(display_text(query, 80))
            rows.insert(-1, [("× 🔍", cb("clear_search", kind))])
        await message.answer(caption, reply_markup=kb.keyboard(rows))

    async def detail(message: Message, state: FSMContext, key: str, *, saved: bool = False):
        await state.set_state(None)
        lang = await language(state)
        await state.update_data(key=key)
        header = text("saved", lang) + "\n\n" if saved else ""
        header += text("current", lang) + f" · {lang.upper()}\n\n"
        rows = [
            [
                (text("edit", lang), cb("edit", str(KEYS.index(key)))),
                (text("preview", lang), cb("sample", str(KEYS.index(key)))),
            ],
            [(text("reset", lang), cb("reset", str(KEYS.index(key))))],
            [(text("back", lang), cb("list", f"{'buttons' if key in BUTTON_KEYS else 'texts'}.0"))],
            [(text("editor", lang), cb("home"))],
        ]
        await message.answer(
            header + "<pre>" + escape(design.text(lang, key)) + "</pre>",
            reply_markup=kb.keyboard(rows),
        )

    async def previews(message: Message, state: FSMContext):
        await state.set_state(None)
        lang = await language(state)
        await message.answer(
            text("preview", lang),
            reply_markup=kb.keyboard(
                [[(text(name, lang), cb("preview", name))] for name in SCREENS]
                + [[(text("editor", lang), cb("home"))]]
            ),
        )

    async def render(message: Message, state: FSMContext, name: str):
        lang = await language(state)
        body, markup = screen(name, design, lang)
        if units(unescape(re.sub(r"<[^>]*>", "", body))) > 4096:
            await message.answer(text("too_long", lang), reply_markup=back(lang))
            return
        await state.set_state(None)
        await state.update_data(preview_role="user" if name == "home_user" else "admin")
        rows = markup.inline_keyboard + back(lang).inline_keyboard
        markup = markup.model_copy(update={"inline_keyboard": rows})
        if name.startswith("home_") and units(body) <= 1000:
            await message.answer_photo(
                FSInputFile(str(files("app").joinpath("assets/home.jpg"))),
                caption=body,
                reply_markup=markup,
            )
        else:
            await message.answer(body, reply_markup=markup)

    @router.message(Command("start", "ui", "menu", "admin", "cancel"))
    async def start(message: Message, state: FSMContext):
        await home(message, state)

    @router.message(Command("preview"))
    async def preview_command(message: Message, state: FSMContext):
        await previews(message, state)

    @router.callback_query(Edit.filter())
    async def callback(callback: CallbackQuery, callback_data: Edit, state: FSMContext):
        assert isinstance(callback.message, Message)
        message = callback.message
        await callback.answer()
        name, value = callback_data.action, callback_data.value
        lang = await language(state)
        if name == "home":
            await home(message, state)
        elif name in {"list", "clear_search"}:
            if name == "clear_search":
                await state.update_data(query="")
                value += ".0"
            kind, page = value.rsplit(".", 1)
            await listing(message, state, kind, int(page))
        elif name == "languages":
            await state.set_state(None)
            await message.answer(
                text("language", lang),
                reply_markup=kb.keyboard(
                    [
                        [
                            ("🇱🇹 LT", cb("lang", "lt")),
                            ("🇬🇧 EN", cb("lang", "en")),
                            ("🇷🇺 RU", cb("lang", "ru")),
                        ],
                        [(text("back", lang), cb("home"))],
                    ]
                ),
            )
        elif name == "lang":
            if value not in LANGUAGES:
                raise ValueError("invalid")
            await state.update_data(lang=value)
            await home(message, state)
        elif name == "search":
            if value not in {"buttons", "texts"}:
                raise ValueError("invalid")
            await state.set_state(Input.search)
            await state.update_data(kind=value)
            await message.answer(text("search_prompt", lang), reply_markup=back(lang))
        elif name in {"key", "edit", "reset", "sample"}:
            if not value.isascii() or not value.isdigit() or not 0 <= int(value) < len(KEYS):
                raise ValueError("invalid")
            key = KEYS[int(value)]
            if name == "reset":
                design.reset_text(lang, key)
                await detail(message, state, key, saved=True)
            elif name == "key":
                await detail(message, state, key)
            elif name == "sample":
                await state.set_state(None)
                rendered = sample(key, design, lang)
                buttons = back(lang)
                if key in BUTTON_KEYS:
                    buttons = kb.keyboard(
                        [[(rendered, cb("demo"))], [(text("editor", lang), cb("home"))]]
                    )
                    rendered = text("preview", lang)
                await message.answer(rendered, reply_markup=buttons)
            else:
                await state.set_state(Input.text)
                await state.update_data(key=key, editing_lang=lang, previous=design.text(lang, key))
                prompt = text("prompt", lang)
                required = fields(design.text(lang, key))
                if required:
                    prompt += "\n\n" + text(
                        "placeholders",
                        lang,
                        fields=escape(", ".join("{" + field + "}" for field in sorted(required))),
                    )
                await message.answer(prompt, reply_markup=back(lang))
        elif name == "previews":
            await previews(message, state)
        elif name == "preview":
            await render(message, state, value)
        elif name == "menus":
            await state.set_state(None)
            await message.answer(
                text("order", lang),
                reply_markup=kb.keyboard(
                    [[(text(menu, lang), cb("layout", menu))] for menu in MENUS]
                    + [[(text("editor", lang), cb("home"))]]
                ),
            )
        elif name in {"layout", "reset_layout"}:
            if value not in MENUS:
                raise ValueError("invalid")
            if name == "reset_layout":
                design.reset_layout(value)
            with design.preview(lang):
                markup = design.markup(value)
            original = [item for row in identifiers(value) for item in row]
            numbered = "\n".join(
                f"{original.index(b.callback_data or '') + 1}. {b.text}"
                for row in markup.inline_keyboard
                for b in row
            )
            current = "\n".join(
                " ".join(str(original.index(b.callback_data or "") + 1) for b in row)
                for row in markup.inline_keyboard
            )
            await state.set_state(Input.layout)
            await state.update_data(menu=value)
            await message.answer(
                escape(numbered)
                + "\n\n"
                + text("layout_prompt", lang, rows="<pre>" + current + "</pre>"),
                reply_markup=kb.keyboard(
                    [
                        [
                            (
                                text("preview", lang),
                                cb("preview", "home_admin" if value == "home" else "admin"),
                            )
                        ],
                        [(text("reset_layout", lang), cb("reset_layout", value))],
                        [(text("editor", lang), cb("home"))],
                    ]
                ),
            )
        elif name == "export":
            await message.answer_document(
                BufferedInputFile(design.export(), filename="safecheck-design.json"),
                reply_markup=back(lang),
            )
        elif name == "demo":
            await message.answer(text("demo", lang), reply_markup=back(lang))
        else:
            await message.answer(text("stale", lang), reply_markup=back(lang))

    @router.callback_query()
    async def demo_callback(callback: CallbackQuery, state: FSMContext):
        assert isinstance(callback.message, Message)
        data = callback.data or ""
        mapping = {
            "home": "home_admin",
            "admin": "admin",
            "info": "info",
            "profile": "profile",
            "scams": "scam",
            "admin_scams": "scam",
        }
        parts = data.split("|")
        if len(parts) == 3 and parts[0] == "sc" and parts[1] in mapping:
            name = mapping[parts[1]]
            if parts[1] == "home" and (await state.get_data()).get("preview_role") == "user":
                name = "home_user"
            await callback.answer()
            await render(callback.message, state, name)
        else:
            await callback.answer(text("demo", await language(state)), show_alert=True)

    @router.message()
    async def input_handler(message: Message, state: FSMContext):
        lang = await language(state)
        if not message.text or message.text.startswith("/"):
            await message.answer(text("demo", lang), reply_markup=back(lang))
            return
        current, data = await state.get_state(), await state.get_data()
        if current == Input.search.state:
            await state.update_data(query=message.text[:80])
            await listing(message, state, data["kind"], 0)
        elif current == Input.text.state:
            key, edited_lang = data["key"], data["editing_lang"]
            if design.text(edited_lang, key) != data["previous"]:
                await message.answer(text("conflict", lang), reply_markup=back(lang))
                await state.set_state(None)
                return
            try:
                design.set_text(edited_lang, key, message.text)
            except ValueError:
                await message.answer(text("invalid", lang), reply_markup=back(lang))
                return
            await detail(message, state, key, saved=True)
        elif current == Input.layout.state:
            try:
                design.set_layout(data["menu"], message.text)
            except ValueError:
                await message.answer(text("layout_invalid", lang), reply_markup=back(lang))
                return
            await state.set_state(None)
            await message.answer(text("saved", lang), reply_markup=back(lang))
            await render(message, state, "home_admin" if data["menu"] == "home" else "admin")
        else:
            await home(message, state)

    return router

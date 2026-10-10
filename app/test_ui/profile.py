"""Validated, atomic design storage, independent from all SAFECheck databases."""

import json
import os
import tempfile
from contextlib import contextmanager
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from string import Formatter
from typing import Any
from urllib.parse import urlparse

from aiogram.types import InlineKeyboardMarkup

from app.bot import keyboards as kb
from app.i18n import CATALOGS, LANGUAGES, use_button_icons, use_language, use_overrides

KEYS = tuple(sorted(CATALOGS["lt"]))
BUTTON_KEYS = frozenset(
    key
    for key in KEYS
    if key.startswith("button.")
    or key.endswith((".button", ".menu"))
    or key
    in {
        "sm.id",
        "sm.username",
        "sm.retry",
        "sm.refresh",
        "sm.remove",
        "sm.unban",
        "sm.unban_retry",
        "sm.save",
        "tm.add",
        "tm.remove",
        "tm.save",
        "tm.confirm",
        "tm.search",
        "tm.clear",
    }
)
MENUS = ("home", "admin")
MAX_FILE = 2_000_000

RESULT_KEYS = (
    "p.profile_title",
    "p.profile_card",
    "p.lookup_status",
    "p.lookup_clear_status",
    "p.no_scam",
    "p.lookup_identity_known",
    "p.lookup_identity_unknown",
    "p.lookup_identity_heading",
    "p.lookup_warning",
    "p.lookup_scam_status",
    "p.lookup_scam_description",
    "p.lookup_reason",
    "p.lookup_reference",
    "p.lookup_scam_date",
    "p.scam_admin_confirmation",
    "p.trusted_status",
    "p.lookup_trusted_manual",
    "p.lookup_trusted_date",
    "p.lookup_trusted_top",
    "p.lookup_trusted_top_description",
    "p.lookup_trusted_role",
    "p.role",
    "p.role_founder",
    "p.role_moderator",
    "p.lookup_username_match",
    "p.lookup_username_match_description",
)


def validate_icon(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or value.startswith("0")
        or len(value) > 20
        or not 0 < int(value) <= 18446744073709551615
    ):
        raise ValueError("icon")
    return value


def fields(template: str) -> set[str]:
    result = set()
    for _, field, specification, conversion in Formatter().parse(template):
        if field is not None:
            if not field.isidentifier() or specification or conversion:
                raise ValueError("placeholders")
            result.add(field)
    return result


def units(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


class TelegramHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        allowed = {
            "b",
            "strong",
            "i",
            "em",
            "u",
            "ins",
            "s",
            "strike",
            "del",
            "code",
            "pre",
            "blockquote",
            "a",
            "span",
            "tg-spoiler",
            "tg-emoji",
        }
        if tag not in allowed:
            raise ValueError("html")
        pairs = dict(attrs)
        if len(pairs) != len(attrs):
            raise ValueError("html")
        valid = not attrs
        if tag == "a":
            valid = set(pairs) == {"href"} and urlparse(pairs["href"] or "").scheme in {
                "https",
                "http",
                "tg",
            }
        elif tag == "span":
            valid = pairs == {"class": "tg-spoiler"}
        elif tag == "tg-emoji":
            valid = set(pairs) == {"emoji-id"} and str(pairs["emoji-id"]).isdigit()
        elif tag == "code" and attrs:
            valid = set(pairs) == {"class"} and str(pairs["class"]).startswith("language-")
        elif tag == "blockquote" and attrs:
            valid = pairs == {"expandable": None}
        if not valid:
            raise ValueError("html")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("html")

    def handle_startendtag(self, tag, attrs):
        raise ValueError("html")

    def handle_data(self, data):
        if "<" in data or "&" in data:
            raise ValueError("html")

    def handle_entityref(self, name):
        if name not in {"lt", "gt", "amp", "quot"}:
            raise ValueError("html")

    def handle_charref(self, name):
        try:
            value = int(name[1:], 16) if name.startswith(("x", "X")) else int(name)
            if not 0 < value <= 0x10FFFF or 0xD800 <= value <= 0xDFFF:
                raise ValueError
        except ValueError:
            raise ValueError("html") from None

    def handle_comment(self, data):
        raise ValueError("html")

    def unknown_decl(self, data):
        raise ValueError("html")

    def handle_decl(self, decl):
        raise ValueError("html")

    def handle_pi(self, data):
        raise ValueError("html")


def validate_text(lang: str, key: str, value: str) -> None:
    if lang not in LANGUAGES or key not in KEYS or not isinstance(value, str):
        raise ValueError("invalid")
    if not value.strip() or units(value) > (64 if key in BUTTON_KEYS else 3500):
        raise ValueError("length")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("invalid")
    if fields(value) != fields(CATALOGS[lang][key]):
        raise ValueError("placeholders")
    if key in BUTTON_KEYS:
        if any(c in value for c in "<>\n\r"):
            raise ValueError("button")
    else:
        parser = TelegramHTML()
        parser.feed(value.format(**{field: "https://example.com" for field in fields(value)}))
        parser.close()
        if parser.stack:
            raise ValueError("html")


def default_markup(menu: str, *, admin: bool = True) -> InlineKeyboardMarkup:
    if menu == "home":
        return kb.home(admin=admin, owner=admin)
    if menu == "admin":
        return kb.admin(owner=True)
    raise ValueError("invalid")


def identifiers(menu: str) -> list[list[str]]:
    return [[b.callback_data or "" for b in row] for row in default_markup(menu).inline_keyboard]


def validate_layout(menu: str, rows: Any) -> None:
    if menu not in MENUS or not isinstance(rows, list) or not rows:
        raise ValueError("layout")
    if any(not isinstance(row, list) or not 1 <= len(row) <= 3 for row in rows):
        raise ValueError("layout")
    expected = [item for row in identifiers(menu) for item in row]
    actual = [item for row in rows for item in row]
    if any(not isinstance(item, str) for item in actual) or sorted(actual) != sorted(expected):
        raise ValueError("layout")


class Design:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "texts": {}, "layouts": {}, "icons": {}}
        if path.exists():
            if path.stat().st_size > MAX_FILE:
                raise ValueError("Design file too large")
            loaded = json.loads(path.read_text())
            if (
                not isinstance(loaded, dict)
                or not {"version", "texts", "layouts"} <= set(loaded) <= set(self.data)
                or loaded["version"] != 1
            ):
                raise ValueError("Invalid design file")
            if not isinstance(loaded["texts"], dict) or not isinstance(loaded["layouts"], dict):
                raise ValueError("Invalid design file")
            for lang, texts in loaded["texts"].items():
                if lang not in LANGUAGES or not isinstance(texts, dict):
                    raise ValueError("Invalid design language")
                for key, value in texts.items():
                    validate_text(lang, key, value)
            for menu, rows in loaded["layouts"].items():
                validate_layout(menu, rows)
            icons = loaded.setdefault("icons", {})
            if not isinstance(icons, dict):
                raise ValueError("Invalid design icons")
            for lang, entries in icons.items():
                if lang not in LANGUAGES or not isinstance(entries, dict):
                    raise ValueError("Invalid icon language")
                for key, icon in entries.items():
                    if key not in BUTTON_KEYS:
                        raise ValueError("Invalid icon key")
                    validate_icon(icon)
            self.data = loaded

    def text(self, lang: str, key: str) -> str:
        return self.data["texts"].get(lang, {}).get(key, CATALOGS[lang][key])

    @contextmanager
    def preview(self, lang: str):
        with (
            use_language(lang),
            use_overrides(self.data["texts"]),
            use_button_icons(self.data["icons"]),
        ):
            yield

    def icon(self, lang: str, key: str) -> str | None:
        return self.data["icons"].get(lang, {}).get(key)

    def set_icon(self, lang: str, key: str, icon: str) -> None:
        if lang not in LANGUAGES or key not in BUTTON_KEYS:
            raise ValueError("icon")
        validate_icon(icon)
        updated = deepcopy(self.data)
        updated["icons"].setdefault(lang, {})[key] = icon
        self.save(updated)

    def reset_icon(self, lang: str, key: str) -> None:
        if lang not in LANGUAGES or key not in BUTTON_KEYS:
            raise ValueError("icon")
        updated = deepcopy(self.data)
        updated["icons"].get(lang, {}).pop(key, None)
        self.save(updated)

    def save(self, updated: dict[str, Any] | None = None) -> None:
        data = self.data if updated is None else updated
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode()
        if len(payload) > MAX_FILE:
            raise ValueError("length")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(dir=self.path.parent, prefix=".design-")
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(payload.decode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
            self.data = data
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def export(self) -> bytes:
        return json.dumps(self.data, ensure_ascii=False, indent=2).encode()

    def set_text(self, lang: str, key: str, value: str) -> None:
        validate_text(lang, key, value)
        updated = deepcopy(self.data)
        updated["texts"].setdefault(lang, {})[key] = value
        self.save(updated)

    def reset_text(self, lang: str, key: str) -> None:
        updated = deepcopy(self.data)
        updated["texts"].get(lang, {}).pop(key, None)
        self.save(updated)

    def set_layout(self, menu: str, value: str) -> None:
        options = [b for row in identifiers(menu) for b in row]
        try:
            if any(
                not number.isascii() or not number.isdigit() or int(number) < 1
                for number in value.split()
            ):
                raise ValueError("layout")
            rows = [
                [options[int(number) - 1] for number in line.split()]
                for line in value.strip().splitlines()
            ]
            validate_layout(menu, rows)
        except (ValueError, IndexError, TypeError):
            raise ValueError("layout") from None
        updated = deepcopy(self.data)
        updated["layouts"][menu] = rows
        self.save(updated)

    def reset_layout(self, menu: str) -> None:
        if menu not in MENUS:
            raise ValueError("layout")
        updated = deepcopy(self.data)
        updated["layouts"].pop(menu, None)
        self.save(updated)

    def markup(self, menu: str, *, admin: bool = True) -> InlineKeyboardMarkup:
        original = default_markup(menu, admin=admin)
        rows = self.data["layouts"].get(menu)
        if rows is None:
            return original
        buttons = {b.callback_data: b for row in original.inline_keyboard for b in row}
        arranged = [[buttons[value] for value in row if value in buttons] for row in rows]
        return InlineKeyboardMarkup(inline_keyboard=[row for row in arranged if row])

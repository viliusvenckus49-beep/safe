"""One locale boundary with per-update context and Lithuanian fallback."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

from app.locales.administrators import CATALOGS as ADMINISTRATORS
from app.locales.core import CATALOGS as CORE
from app.locales.diagnostics import CATALOGS as DIAGNOSTICS
from app.locales.presentation import CATALOGS as PRESENTATION
from app.locales.scam_management import CATALOGS as SCAM_MANAGEMENT
from app.locales.trusted_management import CATALOGS as TRUSTED_MANAGEMENT

LANGUAGES = frozenset({"lt", "en", "ru"})
language: ContextVar[str] = ContextVar("language", default="lt")
overrides: ContextVar[Mapping[str, Mapping[str, str]] | None] = ContextVar(
    "ui_overrides", default=None
)
button_icons: ContextVar[Mapping[str, Mapping[str, str]] | None] = ContextVar(
    "ui_button_icons", default=None
)


class ButtonText(str):
    """Preview-only icon metadata travels with the exact translated button key."""

    icon_custom_emoji_id: str

    def __new__(cls, value: str, icon: str):
        result = super().__new__(cls, value)
        result.icon_custom_emoji_id = icon
        return result


CATALOGS = {
    lang: {
        **CORE[lang],
        **PRESENTATION[lang],
        **ADMINISTRATORS[lang],
        **TRUSTED_MANAGEMENT[lang],
        **SCAM_MANAGEMENT[lang],
        **DIAGNOSTICS[lang],
    }
    for lang in LANGUAGES
}


@contextmanager
def use_language(lang: str | None) -> Iterator[None]:
    token = language.set(lang if lang in LANGUAGES else "lt")
    try:
        yield
    finally:
        language.reset(token)


@contextmanager
def use_overrides(catalog: Mapping[str, Mapping[str, str]]) -> Iterator[None]:
    """Scoped preview overrides; normal bot updates continue using the shipped catalogs."""
    token = overrides.set(catalog)
    try:
        yield
    finally:
        overrides.reset(token)


@contextmanager
def use_button_icons(icons: Mapping[str, Mapping[str, str]]) -> Iterator[None]:
    token = button_icons.set(icons)
    try:
        yield
    finally:
        button_icons.reset(token)


def t(key: str, lang: str | None = None, **values: Any) -> str:
    selected = lang or language.get()
    selected = selected if selected in LANGUAGES else "lt"
    template = (overrides.get() or {}).get(selected, {}).get(key) or CATALOGS[selected].get(key)
    template = template or CATALOGS["lt"].get(key)
    if template is None:
        structlog.get_logger().warning("translation_missing", translation_key=key)
        return CATALOGS[selected]["core.error"]
    try:
        rendered = template.format(**values)
        icon = (button_icons.get() or {}).get(selected, {}).get(key)
        return ButtonText(rendered, icon) if icon else rendered
    except (KeyError, ValueError, IndexError):
        structlog.get_logger().warning("translation_format_failed", translation_key=key)
        return CATALOGS[selected]["core.error"]

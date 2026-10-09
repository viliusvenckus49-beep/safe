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


def t(key: str, lang: str | None = None, **values: Any) -> str:
    selected = lang or language.get()
    selected = selected if selected in LANGUAGES else "lt"
    template = (overrides.get() or {}).get(selected, {}).get(key) or CATALOGS[selected].get(key)
    template = template or CATALOGS["lt"].get(key)
    if template is None:
        structlog.get_logger().warning("translation_missing", translation_key=key)
        return CATALOGS[selected]["core.error"]
    try:
        return template.format(**values)
    except (KeyError, ValueError, IndexError):
        structlog.get_logger().warning("translation_format_failed", translation_key=key)
        return CATALOGS[selected]["core.error"]

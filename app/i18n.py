"""One locale boundary with per-update context and Lithuanian fallback."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog

from app.locales.administrators import CATALOGS as ADMINISTRATORS
from app.locales.core import CATALOGS as CORE
from app.locales.presentation import CATALOGS as PRESENTATION
from app.locales.scam_management import CATALOGS as SCAM_MANAGEMENT
from app.locales.trusted_management import CATALOGS as TRUSTED_MANAGEMENT

LANGUAGES = frozenset({"lt", "en", "ru"})
language: ContextVar[str] = ContextVar("language", default="lt")
CATALOGS = {
    lang: {
        **CORE[lang],
        **PRESENTATION[lang],
        **ADMINISTRATORS[lang],
        **TRUSTED_MANAGEMENT[lang],
        **SCAM_MANAGEMENT[lang],
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


def t(key: str, lang: str | None = None, **values: Any) -> str:
    selected = lang or language.get()
    selected = selected if selected in LANGUAGES else "lt"
    template = CATALOGS[selected].get(key) or CATALOGS["lt"].get(key)
    if template is None:
        structlog.get_logger().warning("translation_missing", translation_key=key)
        return CATALOGS[selected]["core.error"]
    try:
        return template.format(**values)
    except (KeyError, ValueError, IndexError):
        structlog.get_logger().warning("translation_format_failed", translation_key=key)
        return CATALOGS[selected]["core.error"]

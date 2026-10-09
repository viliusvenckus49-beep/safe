"""Render real presentation/builders with synthetic data; never run their handlers."""

from datetime import UTC, datetime
from types import SimpleNamespace

from aiogram.types import InlineKeyboardMarkup

from app import presentation as p
from app.bot import keyboards as kb
from app.bot.scam_admin import controls
from app.i18n import t
from app.test_ui.profile import Design, fields

SCREENS = ("home_user", "home_admin", "admin", "info", "scam", "scam_unknown", "trusted", "profile")


def sample(key: str, design: Design, lang: str) -> str:
    values = {name: "3" for name in fields(design.text(lang, key))}
    values.update(
        {
            name: value
            for name, value in {
                "user": "<b>@demo_user</b> [<code>123456789</code>]",
                "name": "@demo_user",
                "telegram_id": "123456789",
                "heading": "SCAM • REGISTERED",
                "outcome": "Banned 3/5 · Already banned 1 · Queued 1",
                "identity_note": "",
                "reason": "Pavyzdinis tekstas / Sample text",
                "reference": "SC-00001",
                "date": "2026-10-09",
                "title": "Demo",
                "target": "@demo_user",
                "username": "demo_user",
                "comment": "Sample feedback",
                "rows": "Demo 1\nDemo 2",
                "saved": "",
                "status": "TRUSTED",
                "link": "https://example.com",
                "url": "https://example.com",
            }.items()
            if name in values
        }
    )
    with design.preview(lang):
        return t(key, **values)


def screen(name: str, design: Design, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    user = SimpleNamespace(
        id=1, telegram_id=123456789, username="demo_user", display_name="Demo User"
    )
    record = SimpleNamespace(id=1, target=user)
    with design.preview(lang):
        if name.startswith("home_"):
            return p.home(), design.markup("home", admin=name == "home_admin")
        if name == "admin":
            return p.text("admin"), design.markup("admin")
        if name == "info":
            return p.info(), kb.back()
        if name in {"scam", "scam_unknown"}:
            if name == "scam_unknown":
                user.telegram_id = None
            return p.scam_registered(
                user, groups=5, succeeded=4, already_banned=1, pending=1
            ), controls(record)
        if name == "trusted":
            return p.trusted_granted(user), kb.back()
        if name == "profile":
            return p.profile(
                dict(
                    user=user,
                    scam=None,
                    trusted=True,
                    score=12,
                    positive=14,
                    negative=2,
                    trusted_updated_at=datetime.now(UTC),
                )
            ), kb.result("u:1")
        raise ValueError("invalid")

"""Escaped administrator cards, separate from authorization and storage."""

from html import escape

from app.i18n import t
from app.models import User
from app.presentation import display_name


def identity(tg_id: int, user: User | None) -> str:
    name = escape(display_name(user)) if user else t("p.user")
    return f"<b>{name}</b>\nTelegram ID: <code>{tg_id}</code>"


def listing(rows: list[tuple[int, User | None]], owner: int | None) -> str:
    lines = [t("admin_access.title"), t("admin_access.description")]
    for tg_id, user in rows:
        role = t("admin_access.owner" if tg_id == owner else "admin_access.admin")
        lines.append(f"{identity(tg_id, user)} · {role}")
    return "\n\n".join(lines)


def preview(tg_id: int, user: User | None, active: bool) -> str:
    return t(
        "admin_access.preview_add" if active else "admin_access.preview_remove",
        identity=identity(tg_id, user),
    )

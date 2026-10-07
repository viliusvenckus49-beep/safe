"""Bounded, escaped administrative TRUSTED cards."""

from html import escape

from app import presentation as p
from app.i18n import t


def sources(data: dict) -> str:
    keys = []
    if data["manual_trusted"]:
        keys.append("tm.manual")
    if data["top_included"]:
        keys.append("tm.top")
    if data["role"]:
        keys.append("tm.role")
    return " • ".join(t(key) for key in keys) or "—"


def listing(rows: list[dict], query: str = "") -> str:
    text = t("tm.title")
    if query:
        text += "\n\n" + t("tm.query", query=escape(query))
    cards = [
        p.identity(row["user"], max_units=80)
        + "\n"
        + sources(row)
        + "\n"
        + t("tm.active" if row["trusted"] else "tm.inactive")
        for row in rows
    ]
    return text + "\n\n" + ("\n\n".join(cards) if cards else t("tm.empty"))


def detail(data: dict) -> str:
    text = t(
        "tm.detail",
        user=p.identity(data["user"], max_units=120),
        sources=sources(data),
        status=t("tm.active" if data["trusted"] else "tm.inactive"),
    )
    if data["role"]:
        text += "\n\n" + t("tm.role_note")
    return text


def preview(data: dict) -> str:
    text = t("tm.preview", user=p.identity(data["user"], max_units=120))
    if data["role"]:
        text += "\n\n" + t("tm.role_warning")
    return text

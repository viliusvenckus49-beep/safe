"""Group registration, live moderation and retained group history."""

from html import escape

from app.i18n import t


def text(key: str) -> str:
    return t(f"group.{key}")


def group_card(
    title: str, enabled: bool, chat_type: str = "supergroup", *, approved: bool = True
) -> str:
    return t(
        "group.card",
        heading=text("TITLE"),
        title=escape(title),
        status=t(
            "group.pending" if not approved else "group.active" if enabled else "group.inactive"
        ),
        notice=t("group.manage_private"),
        warning=t("group.basic_warning") if chat_type == "group" else "",
    )

"""Localized group management and opt-in recovery presentation."""

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
        notice=text("MEMBERS_NOTICE") + "\n\n" + t("group.manage_private"),
        warning=t("group.basic_warning") if chat_type == "group" else "",
    )


def recovery_preview(title: str, link: str, recipients: int) -> str:
    return t("group.preview", title=escape(title), link=escape(link), recipients=recipients)


def recovery_notification(title: str, link: str, lang: str | None = None) -> str:
    return t("group.notification", lang=lang, title=escape(title), link=escape(link))


def members_backup(title: str, records: list[dict]) -> bytes:
    """Readable UTF-8 export; metadata/history remain in the shared database."""

    def clean(value: object) -> str:
        return " ".join(str(value or "").split())

    lines = [t("group.export_title"), clean(title), ""]
    for position, record in enumerate(records, 1):
        name = clean(record.get("display_name"))
        username = clean(record.get("username"))
        username = f"@{username}" if username else ""
        parts = [part for part in (name, username) if part]
        identity = " — ".join(parts)
        lines.append(f"{position}. {identity} [{record['telegram_id']}]")
    if not records:
        lines.append(t("group.export_empty"))
    return ("\n".join(lines) + "\n").encode("utf-8")

"""Localized presentation boundary; all user content is escaped before HTML rendering."""

from html import escape
from typing import Any

from app.i18n import t

CARD_RULE = "━━━━━━━━━━━━━━━"
FOOTER_RULE = "───────────────"


def home() -> str:
    return t("p.home")


def info() -> str:
    return t("p.info")


def warning() -> str:
    return t("p.warning")


def text(key: str) -> str:
    return t(f"p.{key}")


ERROR_ALIASES = {
    "rep_cooldown": "p.cooldown",
    "report_cooldown": "p.cooldown",
    "invalid_evidence": "p.evidence_invalid",
    "cooldown": "p.cooldown",
    "rate_limit": "p.cooldown",
    "forbidden": "p.denied",
    "admin_private": "p.admin_private",
    "stale_callback": "p.stale",
    "invalid_target": "p.invalid",
    "invalid_reason": "p.reason_invalid",
    "invalid_language": "language.invalid",
}
ERROR_CODES = {
    "sm_conflict",
    "trusted_scam",
    "admin_id_invalid",
    "admin_owner_immutable",
    "group_owner_rights",
    "group_bot_rights",
    "anonymous_identity",
    "invalid_input",
    "self_report",
    "duplicate_report",
    "duplicate_scam",
    "already_moderated",
    "self_rep",
    "duplicate_rep",
    "reciprocal_rep",
    "not_found",
    "already_active",
}


def error(code: str) -> str:
    return t(ERROR_ALIASES.get(code, f"error.{code}" if code in ERROR_CODES else "p.error"))


def display_text(value: str, max_units: int) -> str:
    """Bound Telegram display text without splitting characters or HTML entities."""
    if len(value.encode("utf-16-le")) // 2 <= max_units:
        return value
    result, used = "", 0
    for char in value:
        size = len(char.encode("utf-16-le")) // 2
        if used + size > max_units - 1:
            break
        result += char
        used += size
    return result + "…"


def label(user: Any, *, max_units: int | None = None) -> str:
    value = f"@{user.username}" if user.username else user.display_name
    if not value or value == "Vartotojas":
        value = t("p.user")
    return escape(display_text(value, max_units) if max_units is not None else value)


def display_name(user: Any) -> str:
    name = (user.display_name or "").strip()
    if name and name != "Vartotojas" and name != f"@{user.username}":
        return name
    return f"@{user.username}" if user.username else t("p.user")


def telegram_id_label(user: Any) -> str:
    telegram_id = getattr(user, "telegram_id", None)
    return str(telegram_id) if telegram_id is not None else t("p.id_unknown")


def identity(user: Any, *, max_units: int | None = None) -> str:
    return f"<b>{label(user, max_units=max_units)}</b> [<code>{escape(telegram_id_label(user))}</code>]"


def trusted_granted(user: Any) -> str:
    known = getattr(user, "telegram_id", None) is not None
    return t(
        "p.receipt_trusted",
        heading=t("p.receipt_trusted_verified" if known else "p.receipt_trusted_registered"),
        name=label(user, max_units=120),
        telegram_id=(
            f"<code>{escape(telegram_id_label(user))}</code>" if known else t("p.receipt_unknown")
        ),
        identity_note="" if known else "\n" + t("p.receipt_identity_unknown"),
    )


def scam_registered(
    user: Any,
    *,
    groups: int | None = None,
    succeeded: int = 0,
    failed: int = 0,
    pending: int = 0,
) -> str:
    """Render confirmed ban results; FAILED jobs remain part of the pending outbox."""
    known = getattr(user, "telegram_id", None) is not None
    completed = (
        known
        and groups is not None
        and groups > 0
        and succeeded == groups
        and failed == pending == 0
    )
    if not known:
        outcome = t("p.ban_inactive")
    elif groups is None:
        outcome = t("p.ban_unconfirmed")
    elif groups == 0:
        outcome = t("p.ban_no_groups")
    else:
        outcome = t(
            "p.ban_complete" if completed else "p.ban_partial" if succeeded else "p.ban_pending",
            succeeded=succeeded,
            groups=groups,
            pending=pending,
        )
    return t(
        "p.receipt_scam",
        heading=t("p.receipt_scam_blocked" if completed else "p.receipt_scam_registered"),
        name=label(user, max_units=120),
        telegram_id=(
            f"<code>{escape(telegram_id_label(user))}</code>" if known else t("p.receipt_unknown")
        ),
        outcome=outcome,
    )


def scam_action(user: Any, added: bool) -> str:
    return scam_registered(user) if added else t("p.scam_action_remove", user=identity(user))


def reputation_action(user: Any, value: int) -> str:
    return t(
        "p.rep_action",
        icon="＋" if value > 0 else "−",
        user=identity(user),
        value="+REP" if value > 0 else "-REP",
    )


def moderation_action(user: Any, reference: str, approved: bool) -> str:
    return t("p.mod_approve" if approved else "p.mod_reject", user=identity(user)) + t(
        "p.mod_reference", reference=escape(reference)
    )


def reputation_score(value: int) -> str:
    return f"+{value}" if value >= 0 else f"−{abs(value)}"


def profile(data: dict[str, Any]) -> str:
    user, scam = data["user"], data["scam"]
    trusted = bool(data.get("trusted")) and scam is None
    result = (
        t(
            "p.profile_card",
            title=t("p.profile_title"),
            user=identity(user, max_units=120),
            score=reputation_score(data["score"]),
            positive=data["positive"],
            negative=data["negative"],
        )
        + "\n\n"
    )
    if data.get("role") in {"founder", "moderator"}:
        result += t("p.role", role=t("p.role_" + data["role"])) + "\n\n"
    result += t("p.lookup_status") + "\n"
    if scam:
        result += t("p.lookup_scam_status") + "\n\n" + t("p.lookup_scam_description")
        if getattr(scam, "report_id", None) is None:
            result += "\n\n" + t("p.scam_admin_confirmation")
        elif scam.reason:
            result += "\n\n" + t("p.lookup_reason", reason=escape(display_text(scam.reason, 1800)))
        record_id = getattr(scam, "id", None)
        if isinstance(record_id, int) and record_id > 0:
            result += "\n\n" + t("p.lookup_reference", reference=f"SC-{record_id:05d}")
        result += "\n" + t("p.lookup_scam_date", date=f"{scam.created_at:%Y-%m-%d}")
        result += "\n\n" + FOOTER_RULE + "\n" + t("p.lookup_caution")
    elif data.get("unresolved_scam"):
        unresolved = data["unresolved_scam"]
        result += t("p.lookup_username_match") + "\n\n" + t("p.lookup_username_match_description")
        result += "\n\n" + t("p.lookup_reference", reference=f"SC-{unresolved.id:05d}")
        result += "\n" + t("p.lookup_scam_date", date=f"{unresolved.created_at:%Y-%m-%d}")
    elif trusted:
        top = data.get("trusted_source") == "top"
        result += t("p.lookup_trusted_top" if top else "p.trusted_status")
        result += "\n\n" + t(
            "p.lookup_trusted_top_description"
            if top
            else "p.lookup_trusted_role"
            if data.get("trusted_source") == "role"
            else "p.lookup_trusted_manual"
        )
        updated = data.get("trusted_updated_at")
        if updated is not None and not top:
            result += "\n\n" + t("p.lookup_trusted_date", date=f"{updated:%Y-%m-%d}")
    else:
        result += t("p.lookup_clear_status") + "\n\n" + t("p.no_scam")
    if not scam or user.telegram_id is None:
        result += (
            "\n\n"
            + FOOTER_RULE
            + "\n"
            + t("p.unknown_identity" if user.telegram_id is None else "p.lookup_identity_known")
        )
    if not scam and not trusted:
        result += "\n\n" + warning()
    return result


def preview(data: dict[str, Any]) -> str:
    return t(
        "p.preview",
        target=escape(data["target"]),
        reason=escape(data["reason"]),
        count=len(data.get("evidence", [])),
    )


def submitted(reference: str) -> str:
    return t("p.submitted")


def report_card(details: dict[str, Any]) -> str:
    report = details["report"]
    return t(
        "p.report_card",
        reference=escape(report.reference),
        target=identity(details["target"], max_units=80),
        reporter=identity(details["reporter"], max_units=80),
        reason=escape(report.reason),
        count=len(details["evidence"]),
        date=f"{report.created_at:%Y-%m-%d}",
    )


def leaderboard(rows: list[dict[str, Any]]) -> str:
    entries = []
    for row in rows[:10]:
        user = row["user"]
        name = display_name(user)
        if not any(char.isalnum() for char in name) and user.username:
            name = f"@{user.username}"
        entries.append((display_text(" ".join(name.split()), 80), reputation_score(row["score"])))
    entries += [("—", "—")] * (10 - len(entries))
    lines = [
        f"{index}. {escape(name)} · <b>{score}</b>"
        for index, (name, score) in enumerate(entries, 1)
    ]
    return t("p.leaderboard_title") + "\n\n" + "\n".join(lines)


def scams(rows: list[Any]) -> str:
    cards = []
    for row in rows:
        card = identity(row.target, max_units=80)
        if getattr(row, "report_id", None) is None:
            card += "\n" + t("p.scam_admin_confirmation")
        elif row.reason:
            card += "\n" + escape(display_text(row.reason, 450))
        card += f"\n{row.created_at:%Y-%m-%d}"
        cards.append(card)
    return (
        t("p.scams_title")
        + "\n"
        + CARD_RULE
        + "\n\n"
        + (("\n\n" + FOOTER_RULE + "\n\n").join(cards) or text("scams_empty"))
    )


def stats(data: dict[str, Any]) -> str:
    names = {"users", "reports", "pending", "scams", "scam_records", "reputation_events"}
    return (
        t("p.stats_title")
        + "\n\n"
        + "\n".join(
            f"{t('stat.' + key) if key in names else escape(key)}: <b>{escape(str(value))}</b>"
            for key, value in data.items()
        )
    )


AUDIT_ACTIONS = {
    "trusted_added",
    "trusted_removed",
    "administrator_added",
    "administrator_removed",
    "group_staged",
    "group_registered",
    "group_members_exported",
    "recovery_queued",
    "V1_IMPORT",
    "scam_added",
    "scam_removed",
    "report_approved",
    "report_rejected",
    "reputation_approved",
    "reputation_rejected",
    "reputation_adjust",
    "reputation_reset",
    "reputation_reset_all",
    "top_visibility",
}


def audit(rows: list[Any]) -> str:
    return (
        t("p.audit_title")
        + "\n\n"
        + (
            "\n".join(
                t(
                    "p.audit_row",
                    date=f"{row.created_at:%Y-%m-%d %H:%M}",
                    action=t(
                        "audit." + row.action if row.action in AUDIT_ACTIONS else "audit.unknown"
                    ),
                    actor=row.actor_id,
                )
                for row in rows
            )
            or t("p.audit_empty")
        )
    )


def users(rows: list[Any], *, offset: int = 0) -> str:
    title = t("p.users_title")
    if not rows:
        return title + "\n\n" + t("p.users_empty")
    identifiers = [telegram_id_label(user) for user in rows]
    prefixes = [f"{offset + index}. " for index in range(1, len(rows) + 1)]
    suffixes = [f" [{identifier}]" for identifier in identifiers]

    def units(value: str) -> int:
        return len(value.encode("utf-16-le")) // 2

    fixed = (
        units(title)
        + 2
        + sum(units(prefix + suffix) + 1 for prefix, suffix in zip(prefixes, suffixes, strict=True))
    )
    limit = max(1, min(64, (4000 - fixed) // len(rows)))
    lines = []
    for user, prefix, suffix in zip(rows, prefixes, suffixes, strict=True):
        name = f"@{user.username}" if user.username else display_name(user)
        name = " ".join(name.split())
        bounded = ""
        for char in name:
            if units(bounded + char) > limit - 1:
                bounded += "…"
                break
            bounded += char
        lines.append(f"<code>{escape(prefix + bounded + suffix)}</code>")
    return title + "\n\n" + "\n".join(lines)


def _status(value: str) -> str:
    return t(
        "status."
        + {"PENDING": "pending", "APPROVED": "approved", "REJECTED": "rejected"}.get(
            value, "review"
        )
    )


def reputation_pending(data: dict[str, Any]) -> str:
    request = data["reputation_request"]
    if request is None:
        return text("vote_done") + "\n\n" + profile(data)
    return t(
        "p.rep_pending",
        user=identity(data["user"]),
        value="+REP" if request.value > 0 else "−REP",
        reference=escape(request.reference),
        status=_status(request.status),
    )


def reputation_queue(rows: list[Any], total: int) -> str:
    return t("p.rep_queue", total=total) if rows else text("rep_pending_empty")


def reputation_review(request: Any) -> str:
    result = t(
        "p.rep_review",
        reference=escape(request.reference),
        receiver=identity(request.receiver),
        giver=identity(request.giver),
        value="+REP" if request.value > 0 else "−REP",
        status=_status(request.status),
        date=f"{request.created_at:%Y-%m-%d %H:%M}",
    )
    if getattr(request, "comment", None):
        result += "\n\n" + t("rv.comment", comment=escape(display_text(request.comment, 1500)))
    if request.receiver.telegram_id is None:
        result += "\n\n" + t("p.unknown_identity")
    return result


def reputation_decision(request: Any) -> str:
    return t(
        "p.rep_decision", reference=escape(request.reference), decision=_status(request.status)
    )


def admin_reputation_preview(data: dict[str, Any]) -> str:
    return t(
        "p.admin_rep_preview",
        user=escape(data["target_label"]),
        operation=t("operation." + data["operation"]),
        amount=t("p.amount", amount=data["amount"]) if data.get("amount") else "",
        reason=escape(data["reason"]),
        warning=t("p.reset_warning") if data["operation"] == "rep_reset" else "",
    )


def rep_comment_prompt(user: Any, giver: Any, value: int, *, group: bool) -> str:
    target = identity(user)
    if group:
        target = target.replace("<b>", "<code>").replace("</b>", "</code>")
    text = t("rv.prompt", user=target, value="+REP" if value == 1 else "−REP")
    if group:
        actor = f'<a href="tg://user?id={giver.telegram_id}">{escape(display_text(display_name(giver), 80))}</a>'
        text += "\n\n" + t("rv.giver", user=actor)
    return text

"""Escaped Telegram HTML cards; no reputation or authorization decisions here."""

from html import escape

from app.bot.redsafe_profile import BRAND
from app.i18n import language, t
from app.merchant_catalog import utc
from app.presentation import display_name, display_text


def heading(key):
    return f"<b>{BRAND}</b>\n\n<b>{t('mc.' + key)}</b>"


def identity(user, limit=80):
    label = display_text("@" + user.username if user.username else display_name(user), limit)
    if user.telegram_id is None:
        return escape(label) + " · " + t("mc.limited")
    return f'<a href="tg://user?id={user.telegram_id}">{escape(label)}</a> [<code>{user.telegram_id}</code>]'


def root(settings):
    copy = settings.copy.get(language.get(), {})
    title = escape(copy["title"]) if "title" in copy else t("mc.title")
    intro = escape(copy["intro"]) if "intro" in copy else t("mc.intro")
    return f"<b>{BRAND}</b>\n\n<b>{title}</b>\n\n<i>{intro}</i>\n\n<b>{t('mc.choose')}</b>"


def city_name(code):
    return t("mc." + code) if code else t("mc.not_set")


def stats(data):
    return "\n".join(
        (
            f"<b>{t('mc.score')}:</b> <b>{data['score']:+d}</b>",
            f"<b>{t('mc.positive')}:</b> <b>{data['positive']}</b>",
            f"<b>{t('mc.negative')}:</b> <b>{data['negative']}</b>",
        )
    )


def listing(rows, total, page, city="", query="", admin=False):
    result = heading("admin_list" if admin else "list_title")
    if city:
        result += "\n\n" + f"<b>{t('mc.city')}:</b> {city_name(city)}"
    if query:
        result += "\n\n" + t("mc.query", query=escape(query))
    result += "\n\n" + t("mc.count", count=total)
    if not rows:
        return (
            result
            + "\n\n"
            + (t("mc.empty") if admin else t("mc.no_results") if query else t("mc.empty_city"))
        )
    for i, (entry, user, score) in enumerate(rows, page * 6 + 1):
        label = entry.name or user.display_name
        result += f"\n\n<b>{i:02d} · {escape(display_text(label, 64))}</b>\n⭐ {t('mc.score')}: <b>{score:+d}</b>"
        if admin:
            result += " · " + t("mc.status_" + entry.status)
    return result


def fields(obj):
    return "\n\n".join(
        (
            f"<b>{t('mc.name')}:</b> <b>{escape(obj.name or t('mc.not_set'))}</b>",
            f"<b>{t('mc.city')}:</b> {city_name(obj.city_code)}",
            f"<b>{t('mc.description')}:</b> {escape(obj.description or t('mc.not_set'))}",
        )
    )


def profile(entry, data):
    result = (
        heading("profile_title")
        + "\n\n"
        + f"<b>{t('mc.name')}:</b> <b>{escape(entry.name)}</b>\n\n<b>{t('mc.telegram')}:</b> {identity(entry.owner)}\n\n<b>{t('mc.city')}:</b> {city_name(entry.city_code)}\n\n<b>{t('mc.description')}:</b> {escape(entry.description)}"
    )
    result += "\n\n<b>" + t("mc.rep_title") + "</b>\n\n" + stats(data)
    result += "\n\n<b>" + t("mc.status") + ":</b> " + t("mc.status_APPROVED")
    if data["trusted"]:
        result += "\n" + t("mc.trusted")
    return result + "\n\n<i>" + t("mc.disclaimer") + "</i>"


def reviews(entry, data, rows, publish_authors):
    result = (
        heading("reviews_title")
        + "\n\n"
        + identity(entry.owner)
        + "\n\n"
        + stats(data)
        + "\n\n<b>"
        + t("mc.history")
        + "</b>"
    )
    if not rows:
        result += "\n\n" + t("mc.no_reviews")
    for row in rows:
        sign = "👍 +REP" if row["value"] == 1 else "👎 −REP"
        result += "\n\n<b>" + sign + "</b> · " + utc(row["date"]).strftime("%d.%m.%Y")
        result += "\n\n<i>" + escape(row["comment"] or t("mc.legacy_review")) + "</i>"
        author = identity(row["User"], 48) if publish_authors else t("mc.anonymous")
        result += "\n\n" + t("mc.review_author", author=author)
    return result + "\n\n<i>" + t("mc.corrections") + "</i>"


def private_entry(entry, proposal=None):
    result = (
        heading("mine")
        + "\n\n"
        + identity(entry.owner)
        + "\n\n"
        + fields(entry)
        + "\n\n<b>"
        + t("mc.status")
        + ":</b> "
        + t("mc.status_" + entry.status)
    )
    if entry.reason:
        result += "\n\n<b>" + t("mc.reason") + ":</b> " + escape(display_text(entry.reason, 600))
    if proposal:
        result += "\n\n" + t("mc." + proposal.kind) + " · " + t("mc.status_" + proposal.status)
        if proposal.reason:
            result += (
                "\n\n<b>" + t("mc.reason") + ":</b> " + escape(display_text(proposal.reason, 600))
            )
    return result


def proposal_card(proposal, entry):
    result = (
        heading("draft_title")
        + "\n\n"
        + identity(entry.owner)
        + "\n\n<b>"
        + t("mc." + proposal.kind)
        + "</b>\n\n"
        + fields(entry if proposal.kind == "REMOVE" else proposal)
    )
    result += "\n\n<b>" + t("mc.status") + ":</b> " + t("mc.status_" + proposal.status)
    if proposal.status == "PENDING":
        result += "\n\n" + t("mc.pending_notice")
    if proposal.reason:
        result += "\n\n<b>" + t("mc.reason") + ":</b> " + escape(proposal.reason)
    return result


def settings_card(row):
    copy = row.copy.get(language.get(), {})
    return (
        heading("settings")
        + "\n\n"
        + t("mc.privacy_notice")
        + "\n\n<b>"
        + t("mc.setting_title")
        + ":</b> "
        + escape(copy.get("title", t("mc.title")))
        + "\n\n<b>"
        + t("mc.setting_intro")
        + ":</b> "
        + escape(copy.get("intro", t("mc.intro")))
    )

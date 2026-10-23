"""Read-only navigation over existing admin services and durable records."""

from html import escape

from sqlalchemy import func, or_, select

from app import presentation as p
from app.ads_services import AdsService
from app.bot.callbacks import AdminAccess, AutoAd
from app.bot.group_keyboards import GroupAction
from app.bot.keyboards import action, keyboard
from app.bot.screens import clear_flow, render
from app.i18n import t
from app.models import AdCampaign, AdCustomerGrant, AdPublicationAttempt, AuditEvent
from app.services import DomainError


def route(section):
    return action("admin_section", section)


def heading(key):
    return "🛡 <b>CRIMSON SAFECHECK™</b>\n\n<b>" + t(key) + "</b>"


def section_keyboard(section):
    if section == "scam":
        rows = [
            [(t("button.pending"), action("pending"))],
            [(t("button.registry"), action("admin_scams", "0"))],
            [(t("button.add_scam"), action("add_sc")), (t("button.remove_scam"), action("del_sc"))],
            [(t("tm.menu"), action("trusted_admin"))],
            [(t("nav.scam_history"), action("category_audit", "scam:0"))],
        ]
    elif section == "reputation":
        rows = [
            [(t("button.rep_pending"), action("rep_pending", "0"))],
            [(t("button.rep_add"), action("rep_add")), (t("button.rep_sub"), action("rep_sub"))],
            [(t("button.rep_reset"), action("rep_reset"))],
            [(t("nav.top"), route("top"))],
            [(t("nav.rep_history"), action("category_audit", "reputation:0"))],
        ]
    elif section == "top":
        rows = [
            [(t("button.top_include"), action("top_include"))],
            [(t("button.top_exclude"), action("top_exclude"))],
        ]
    elif section == "users":
        rows = [
            [(t("button.users"), action("users", "0"))],
            [(t("nav.search_user"), action("user_search"))],
            [(t("nav.view_profile"), action("user_profile"))],
        ]
    elif section == "system":
        rows = [
            [(t("button.stats"), action("stats"))],
            [(t("button.status"), action("status"))],
            [(t("button.audit"), action("audit"))],
        ]
    elif section == "owner":
        rows = [
            [(t("admin_access.button"), AdminAccess(action="list").pack())],
            [(t("button.groups"), GroupAction(action="list").pack())],
        ]
    else:
        raise DomainError("stale_callback")
    rows += [
        [
            (
                t("button.back_admin") if section != "top" else t("nav.reputation"),
                action("admin") if section != "top" else route("reputation"),
            )
        ]
    ]
    return keyboard(rows)


async def open_section(message, state, service, actor, section):
    await service.require_admin(actor)
    if message.chat.type != "private" or message.chat.id != actor:
        raise DomainError("forbidden")
    if section == "owner":
        service.access.require_owner(actor)
    if section.startswith("ads_"):
        await ads_overview(message, state, service, actor, section)
        return
    markup = section_keyboard(section)
    await clear_flow(state)
    await render(message, heading("nav." + section) + "\n\n" + t("nav.choose"), reply_markup=markup)


async def audit_listing(message, state, service, actor, category, page=0):
    await service.require_admin(actor)
    if category not in {"scam", "reputation"} or not 0 <= page <= 100000:
        raise DomainError("stale_callback")
    names = (
        ("scam", "report", "trusted") if category == "scam" else ("reputation", "top_visibility")
    )
    where = or_(*(AuditEvent.action.startswith(name) for name in names))
    total = (
        await service.session.scalar(select(func.count()).select_from(AuditEvent).where(where)) or 0
    )
    page = min(page, max(0, (total - 1) // 5))
    entries = list(
        await service.session.scalars(
            select(AuditEvent).where(where).order_by(AuditEvent.id.desc()).offset(page * 5).limit(5)
        )
    )
    rows = [
        [
            ("◀", action("category_audit", f"{category}:{max(0, page - 1)}")),
            (f"{page + 1}/{max(1, (total + 4) // 5)}", action("noop")),
            (
                "▶",
                action("category_audit", f"{category}:{min(max(0, (total - 1) // 5), page + 1)}"),
            ),
        ],
        [(t("button.back"), action("rep_admin") if category == "reputation" else route(category))],
    ]
    await clear_flow(state)
    await render(message, p.audit(entries), reply_markup=keyboard(rows))


async def ads_overview(message, state, service, actor, section, page=0):
    # Global admin status alone never bypasses the existing ADS authorizer.
    await AdsService(service.settings, service.session).authorize(actor)
    if section not in {"ads_clients", "ads_stats", "ads_settings"}:
        raise DomainError("stale_callback")
    await clear_flow(state)
    rows = []
    text = heading(
        "nav.clients"
        if section == "ads_clients"
        else "button.stats"
        if section == "ads_stats"
        else "nav.settings"
    )
    if section == "ads_stats":
        records = await service.session.execute(
            select(AdPublicationAttempt.status, func.count()).group_by(AdPublicationAttempt.status)
        )
        counts = dict(records.all())
        text += "\n\n" + t(
            "nav.ads_stats",
            total=sum(counts.values()),
            sent=counts.get("SENT", 0),
            failed=counts.get("FAILED", 0),
            uncertain=counts.get("UNCERTAIN", 0),
            cancelled=counts.get("CANCELLED", 0),
        )
        rows += [[(t("nav.ads_list"), AutoAd(action="list").pack())]]
    elif section == "ads_settings":
        text += "\n\n" + t("nav.ads_settings_note")
        rows += [[(t("nav.ads_list"), AutoAd(action="list").pack())]]
    else:
        # Paginated actual grants: revoked/expired capabilities are never revived here.
        latest = select(func.max(AdCustomerGrant.id)).group_by(AdCustomerGrant.campaign_id)
        where = (
            AdCustomerGrant.id.in_(latest),
            AdCustomerGrant.status == "ACTIVE",
            AdCampaign.status == "DRAFT",
        )
        total = (
            await service.session.scalar(
                select(func.count())
                .select_from(AdCampaign)
                .join(AdCustomerGrant, AdCustomerGrant.campaign_id == AdCampaign.id)
                .where(*where)
            )
            or 0
        )
        page = min(page, max(0, (total - 1) // 4))
        valid = list(
            await service.session.execute(
                select(AdCampaign, AdCustomerGrant)
                .join(AdCustomerGrant, AdCustomerGrant.campaign_id == AdCampaign.id)
                .where(*where)
                .order_by(AdCampaign.id)
                .offset(page * 4)
                .limit(4)
            )
        )
        text += "\n\n" + (t("nav.choose") if valid else t("nav.empty"))
        for campaign, grant in valid:
            text += "\n" + escape(campaign.internal_name) + f" · <code>{grant.customer_id}</code>"
            rows.append(
                [(campaign.internal_name[:55], AutoAd(action="client", ad=campaign.id).pack())]
            )
        if page:
            rows.append([("◀", action("ads_clients_page", str(page - 1)))])
        if total > (page + 1) * 4:
            rows.append([("▶", action("ads_clients_page", str(page + 1)))])
    rows += [[(t("button.back"), AutoAd(action="root").pack())]]
    await render(message, text, reply_markup=keyboard(rows))

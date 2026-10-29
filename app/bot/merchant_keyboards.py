"""Compact callback keyboards. User search text never enters callback_data."""

from app.bot.callbacks import Merchant
from app.bot.keyboards import action, keyboard
from app.i18n import t
from app.merchant_catalog import CITIES
from app.presentation import display_text


def cb(name, entry=0, *, page=0, value="", version=0):
    return Merchant(action=name, entry=entry, page=page, value=value, version=version).pack()


def button(key, name, entry=0, **kwargs):
    return t("mc." + key), cb(name, entry, **kwargs)


def back(admin=False):
    return button("manage_back" if admin else "back", "a_root" if admin else "root")


def root(mine=False):
    rows = [
        [button("cities", "cities")],
        [button("search", "search")],
    ]
    if mine:
        rows.append([button("mine", "mine")])
    return keyboard([*rows, [(t("mc.home"), action("home"))]])


def cities():
    rows = [
        [button(code, "list", value=code) for code in CITIES[i : i + 2]]
        for i in range(0, len(CITIES), 2)
    ]
    return keyboard([*rows, [button("all_merchants", "list")], [back()]])


def pages(route, page, total, limit, *, entry=0, value=""):
    last = max(0, (total - 1) // limit)
    return [
        ("◀", cb(route, entry, page=max(0, page - 1), value=value)),
        (f"{page + 1} / {last + 1}", cb("noop")),
        ("▶", cb(route, entry, page=min(last, page + 1), value=value)),
    ]


def listing(rows, total, page, *, admin=False):
    route = "a_list" if admin else "list"
    target = "a_entry" if admin else "profile"
    result = [
        [(display_text(entry.name or user.display_name, 48), cb(target, entry.id))]
        for entry, user, score in rows
    ]
    result.append(pages(route, page, total, 6))
    result.append([button("search", "a_search" if admin else "search")])
    result.append([back(True)] if admin else [button("cities_back", "cities")])
    return keyboard(result)


def profile(entry):
    return keyboard(
        [
            [button("reviews", "reviews", entry.id)],
            [button("check", "check", entry.id), button("activity", "activity", entry.id)],
            [button("list_back", "list", page=-1)],
        ]
    )


def reviews(entry, page, total, sign):
    return keyboard(
        [
            [
                button("filter_" + value, "reviews", entry.id, value=value)
                for value in ("all", "pos", "neg")
            ],
            pages("reviews", page, total, 1, entry=entry.id, value=sign),
            [button("profile_back", "profile", entry.id)],
            [button("check", "check", entry.id)],
            [button("list_back", "list", page=-1)],
        ]
    )


def terms(admin=False):
    return keyboard([[button("agree", "a_target" if admin else "begin")], [back(admin)]])


def draft(proposal, admin=False):
    prefix = "a_" if admin else ""
    rows = []
    if proposal.status == "DRAFT" and proposal.kind == "REMOVE":
        rows = [[button("submit", prefix + "submit", proposal.id, version=proposal.version)]]
    elif proposal.status == "DRAFT" and (admin or proposal.kind != "CREATE"):
        rows = [
            [
                button("edit_name", prefix + "name", proposal.id, version=proposal.version),
                button(
                    "edit_description",
                    prefix + "description",
                    proposal.id,
                    version=proposal.version,
                ),
            ],
            [button("edit_city", prefix + "city", proposal.id, version=proposal.version)],
            [button("submit", prefix + "submit", proposal.id, version=proposal.version)],
        ]
    elif proposal.status == "PENDING" and admin:
        rows = [
            [
                button("approve", "a_approve", proposal.id, version=proposal.version),
                button("reject", "a_reject", proposal.id, version=proposal.version),
            ]
        ]
    if not admin and proposal.status in ("DRAFT", "PENDING"):
        rows.append([button("cancel_request", "cancel", proposal.id, version=proposal.version)])
    rows.append([back(True)] if admin else [button("back_mine", "mine")])
    return keyboard(rows)


def draft_cities(proposal, admin=False):
    return keyboard(
        [
            [
                button(
                    code,
                    "a_setcity" if admin else "setcity",
                    proposal.id,
                    value=code,
                    version=proposal.version,
                )
                for code in CITIES[i : i + 2]
            ]
            for i in range(0, len(CITIES), 2)
        ]
        + [[button("preview", "a_draft" if admin else "draft", proposal.id)]]
    )


def mine(entry, proposal, *, blocked=False):
    rows = []
    if proposal:
        rows.append([button("preview", "draft", proposal.id)])
    if entry.status in ("APPROVED", "SUSPENDED") and (
        not proposal or proposal.status not in ("DRAFT", "PENDING")
    ):
        rows.append([button("change", "edit", entry.id)])
        rows.append([button("remove_request", "removal", entry.id)])
    if entry.status == "APPROVED" and not blocked:
        rows.append([button("profile_back", "profile", entry.id)])
    return keyboard([*rows, [back()]])


def admin_root():
    return keyboard(
        [
            [button("queue", "a_queue")],
            [button("admin_list", "a_list", page=-1)],
            [button("add", "a_terms")],
            [button("search", "a_search")],
            [button("settings", "a_settings")],
            [button("audit", "a_audit")],
            [(t("mc.admin_back"), action("admin"))],
        ]
    )


def admin_entry(entry):
    rows = (
        [[button("change", "a_edit", entry.id)]]
        if entry.status in ("APPROVED", "SUSPENDED")
        else []
    )
    if entry.status == "APPROVED":
        rows.append([button("suspend", "a_suspend", entry.id, version=entry.version)])
    if entry.status == "SUSPENDED":
        rows.append([button("restore", "a_restore", entry.id, version=entry.version)])
    if entry.status in ("APPROVED", "SUSPENDED"):
        rows.append([button("remove", "a_remove", entry.id, version=entry.version)])
    rows.append([button("audit", "a_audit", entry.id)])
    return keyboard([*rows, [button("admin_list", "a_list", page=-1)], [back(True)]])


def settings(row):
    rows = []
    for field in ("enabled", "registrations", "publish_authors"):
        rows.append(
            [
                (
                    f"{'✓' if getattr(row, field) else '×'} {t('mc.' + field)}",
                    cb("a_toggle", value=field, version=row.version),
                )
            ]
        )
    rows.append(
        [
            button("setting_title", "a_copy", value="title", version=row.version),
            button("setting_intro", "a_copy", value="intro", version=row.version),
        ]
    )
    return keyboard([*rows, [back(True)]])

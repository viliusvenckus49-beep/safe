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
    if admin:
        result.append([button("search", "a_search")])
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


def mine(entry, *, blocked=False):
    rows = (
        [[button("profile_back", "profile", entry.id)]]
        if entry.status == "APPROVED" and not blocked
        else []
    )
    return keyboard([*rows, [back()]])


def editor(session):
    args = dict(entry=session["entry"], version=session["revision"])
    return keyboard(
        [
            [
                button("edit_name", "a_field", value="name", **args),
                button("edit_description", "a_field", value="description", **args),
            ],
            [
                button("edit_city", "a_field", value="city_code", **args),
                button("edit_contact", "a_field", value="contact", **args),
            ],
            [button("confirm", "a_commit", **args)],
            [button("cancel_edit", "a_discard", **args)],
        ]
    )


def field_back(session):
    return button("step_back", "a_editback", session["entry"], version=session["revision"])


def editor_cities(session):
    return keyboard(
        [
            [
                button(
                    code, "a_cityvalue", session["entry"], value=code, version=session["revision"]
                )
                for code in CITIES[i : i + 2]
            ]
            for i in range(0, len(CITIES), 2)
        ]
        + [[field_back(session)]]
    )


def admin_root():
    return keyboard(
        [
            [button("admin_list", "a_list", page=-1)],
            [button("add", "a_create")],
            [button("search", "a_search")],
            [button("settings", "a_settings")],
            [(t("mc.admin_back"), action("admin"))],
        ]
    )


def admin_entry(entry):
    rows = []
    if entry.status != "REMOVED":
        rows.append([button("change", "a_editor", entry.id, version=entry.version)])
        if entry.status == "APPROVED":
            rows.append([button("suspend", "a_suspend", entry.id, version=entry.version)])
        elif entry.status == "SUSPENDED":
            rows.append([button("restore", "a_restore", entry.id, version=entry.version)])
        rows.append([button("remove", "a_remove", entry.id, version=entry.version)])
    return keyboard([*rows, [back(True)]])


def settings(row):
    rows = []
    for field in ("enabled", "publish_authors"):
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

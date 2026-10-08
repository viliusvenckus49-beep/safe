from typing import Any

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks import (
    Action,
    AdminAccess,
    AdminHelp,
    AdminRepStep,
    Language,
    Moderation,
    ReportStep,
    ReputationModeration,
)
from app.bot.group_keyboards import GroupAction
from app.i18n import t
from app.presentation import display_name, display_text, leaderboard_name


def keyboard(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        for label, callback in row:
            builder.button(text=label, callback_data=callback)
        builder.adjust(*[len(row) for row in rows])
    return builder.as_markup()


def action(name: str, value: str = "") -> str:
    return Action(name=name, value=value).pack()


def home(admin: bool = False, *, private: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [(t("button.lookup"), action("lookup"))],
        [(t("button.rep"), action("rep")), (t("button.report"), action("report"))],
        [(t("button.top"), action("top"))]
        + ([(t("button.scams"), action("scams", "0"))] if private else []),
        [(t("button.profile"), action("profile")), (t("button.info"), action("info"))],
    ]
    if private:
        rows.append([(t("button.recovery"), GroupAction(action="subscriptions").pack())])
    rows.append([(t("button.language"), action("language"))])
    rows.append([(t("button.close"), action("close"))])
    return keyboard(rows)


def back(destination: str = "home", value: str = "") -> InlineKeyboardMarkup:
    return keyboard([[(t("button.back"), action(destination, value))]])


def navigation(destination: str = "home", value: str = "") -> InlineKeyboardMarkup:
    """Navigation for an unfinished input flow, never a completed notification."""
    return keyboard(
        [
            [(t("button.back"), action(destination, value))],
            [(t("button.cancel"), action("close"))],
        ]
    )


def result(
    target: str = "", *, back_name: str | None = None, back_value: str = ""
) -> InlineKeyboardMarkup:
    rows = [[(t("button.lookup_other"), action("lookup"))]]
    if target:
        rows.append(
            [
                (t("button.positive"), action("vote+", target)),
                (t("button.negative"), action("vote-", target)),
            ]
        )
    if back_name:
        rows.append([(t("button.back"), action(back_name, back_value))])
    else:
        rows.append([(t("button.cancel"), action("close"))])
    return keyboard(rows)


def report(nonce: str, stage: str) -> InlineKeyboardMarkup:

    def step(name: str) -> str:
        return ReportStep(action=name, nonce=nonce).pack()

    rows = []
    if stage == "evidence":
        rows.append([(t("button.preview"), step("preview"))])
    if stage == "preview":
        rows.append([(t("button.submit"), step("submit"))])
        rows.append([(t("button.reason"), step("edit")), (t("button.evidence"), step("evidence"))])
    rows.append([(t("button.back"), step("back") if stage != "target" else action("home"))])
    rows.append([(t("button.cancel"), action("close"))])
    return keyboard(rows)


def admin(owner: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [(t("tm.menu"), action("trusted_admin"))],
        [(t("button.pending"), action("pending"))],
        [(t("button.rep_pending"), action("rep_pending", "0"))],
        [(t("button.rep_admin"), action("rep_admin"))],
        [(t("button.registry"), action("admin_scams", "0"))],
        [(t("button.add_scam"), action("add_sc")), (t("button.remove_scam"), action("del_sc"))],
        [(t("button.users"), action("users", "0")), (t("button.stats"), action("stats"))],
        [(t("button.audit"), action("audit"))],
        [(t("admin_help.button"), AdminHelp().pack())],
    ]
    if owner:
        rows.append([(t("admin_access.button"), AdminAccess(action="list").pack())])
    rows.append([(t("button.home"), action("home"))])
    return keyboard(rows)


def moderation(reference: str) -> InlineKeyboardMarkup:

    def callback(name: str) -> str:
        return Moderation(action=name, reference=reference).pack()

    return keyboard(
        [
            [(t("button.approve"), callback("approve")), (t("button.reject"), callback("reject"))],
            [
                (t("button.evidence"), callback("evidence")),
                (t("button.next"), action("pending", reference)),
            ],
            [(t("button.back"), action("admin"))],
        ]
    )


def pages(page: int, total: int, *, admin: bool = False) -> InlineKeyboardMarkup:
    last = max(0, (total - 1) // 5)
    route = "admin_scams" if admin else "scams"
    return keyboard(
        [
            [
                ("‹", action(route, str(max(0, page - 1)))),
                (f"{page + 1} / {last + 1}", action("noop")),
                ("›", action(route, str(min(last, page + 1)))),
            ],
            [(t("button.search"), action("lookup", f"{route}:{page}"))],
            [(t("button.back"), action("admin" if admin else "home"))],
        ]
    )


def admin_pages(name: str, page: int, total: int, *, page_size: int = 5) -> InlineKeyboardMarkup:
    last = max(0, (total - 1) // page_size)
    return keyboard(
        [
            [
                ("‹", action(name, str(max(0, page - 1)))),
                (f"{page + 1} / {last + 1}", action("noop")),
                ("›", action(name, str(min(last, page + 1)))),
            ],
            [(t("button.back"), action("admin"))],
        ]
    )


def leaderboard(rows: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, row in enumerate(rows, 1):
        user = row["user"]
        name = display_text(leaderboard_name(user), 45)
        if user.username:
            builder.button(text=f"{index}. {name}", url=f"https://t.me/{user.username}")
        elif user.telegram_id:
            builder.button(text=f"{index}. {name}", url=f"tg://user?id={user.telegram_id}")
        else:
            builder.button(text=f"{index}. {name}", callback_data=action("lookup"))
    builder.button(text=t("button.home"), callback_data=action("home"))
    builder.adjust(1)
    return builder.as_markup()


def reputation_admin() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("button.rep_add"), action("rep_add")), (t("button.rep_sub"), action("rep_sub"))],
            [(t("button.rep_reset"), action("rep_reset"))],
            [
                (t("button.top_include"), action("top_include")),
                (t("button.top_exclude"), action("top_exclude")),
            ],
            [(t("button.back"), action("admin"))],
        ]
    )


def reputation_review(reference: str, page: int) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [
                (
                    t("button.approve"),
                    ReputationModeration(action="approve", reference=reference).pack(),
                ),
                (
                    t("button.reject"),
                    ReputationModeration(action="reject", reference=reference).pack(),
                ),
            ],
            [(t("button.back"), action("rep_pending", str(page)))],
        ]
    )


def reputation_queue(rows: list[Any], page: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for request in rows:
        builder.button(
            text=f"＋ {request.reference}", callback_data=action("rep_review", request.reference)
        )
    builder.adjust(1)
    navigation = admin_pages("rep_pending", page, total)
    for row in navigation.inline_keyboard:
        builder.row(*row)
    return builder.as_markup()


def admin_rep_preview(nonce: str) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("button.confirm_action"), AdminRepStep(action="submit", nonce=nonce).pack())],
            [(t("button.back"), AdminRepStep(action="back", nonce=nonce).pack())],
            [(t("button.cancel"), action("close"))],
        ]
    )


def admin_rep_navigation(nonce: str, back: bool = True) -> InlineKeyboardMarkup:
    rows = []
    rows.append(
        [
            (
                t("button.back"),
                AdminRepStep(action="back", nonce=nonce).pack() if back else action("rep_admin"),
            )
        ]
    )
    rows.append([(t("button.cancel"), action("close"))])
    return keyboard(rows)


def languages() -> InlineKeyboardMarkup:
    return keyboard(
        [
            [("🇱🇹 Lietuvių", Language(lang="lt").pack())],
            [("🇬🇧 English", Language(lang="en").pack())],
            [("🇷🇺 Русский", Language(lang="ru").pack())],
            [(t("button.home"), action("home"))],
        ]
    )


HELP_SECTIONS = ("basics", "reputation", "reports", "scam", "top", "permissions")


def administrator_help(section: str = "menu") -> InlineKeyboardMarkup:
    rows = (
        [
            [(t("admin_help.section." + topic), AdminHelp(section=topic).pack())]
            for topic in HELP_SECTIONS
        ]
        if section == "menu"
        else [[(t("button.back"), AdminHelp().pack())]]
    )
    if section == "menu":
        rows.append([(t("button.back"), action("admin"))])
    return keyboard(rows)


def administrator_menu(rows, page: int, total: int, owner: int | None) -> InlineKeyboardMarkup:
    buttons = [[(t("admin_access.add"), AdminAccess(action="add").pack())]]
    for tg_id, user in rows:
        if tg_id != owner:
            name = display_name(user) if user else str(tg_id)
            buttons.append(
                [
                    (
                        t("admin_access.remove_user", name=name[:32]),
                        AdminAccess(action="remove", value=str(tg_id)).pack(),
                    )
                ]
            )
    pages = max(1, (total + 7) // 8)
    navigation = []
    if page > 0:
        navigation.append(("‹", AdminAccess(action="list", value=str(page - 1)).pack()))
    navigation.append((f"{page + 1} / {pages}", action("noop")))
    if page + 1 < pages:
        navigation.append(("›", AdminAccess(action="list", value=str(page + 1)).pack()))
    buttons.append(navigation)
    buttons.append([(t("button.back"), action("admin"))])
    return keyboard(buttons)


def administrator_preview(nonce: str) -> InlineKeyboardMarkup:
    return keyboard(
        [
            [(t("button.confirm_action"), AdminAccess(action="submit", value=nonce).pack())],
            [(t("button.back"), AdminAccess(action="list").pack())],
            [(t("button.cancel"), action("close"))],
        ]
    )


def administrator_input(*, completed: bool = False) -> InlineKeyboardMarkup:
    rows = [[(t("button.back"), AdminAccess(action="list").pack())]]
    if not completed:
        rows.append([(t("button.cancel"), action("close"))])
    return keyboard(rows)

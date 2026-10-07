from aiogram.types import InlineKeyboardMarkup

from app import presentation as p
from app.bot.callbacks import TrustedAdmin
from app.bot.keyboards import action, keyboard
from app.i18n import t


def callback(name: str, value: str = "") -> str:
    return TrustedAdmin(action=name, value=value).pack()


def page(rows: list[dict], current: int, total: int, query: str) -> InlineKeyboardMarkup:
    buttons = [
        [(p.display_text(p.display_name(row["user"]), 40), callback("view", str(row["user"].id)))]
        for row in rows
    ]
    pages = max(1, (total + 7) // 8)
    navigation = []
    if current:
        navigation.append(("◀️", callback("page", str(current - 1))))
    navigation.append((f"{current + 1} / {pages}", callback("noop")))
    if current + 1 < pages:
        navigation.append(("▶️", callback("page", str(current + 1))))
    buttons.append(navigation)
    buttons.append([(t("tm.search"), callback("search"))])
    if query:
        buttons.append([(t("tm.clear"), callback("clear"))])
    buttons.append([(t("button.back"), action("admin"))])
    return keyboard(buttons)


def back(page_number: int, *, persistent: bool = False):
    name = "page_receipt" if persistent else "page"
    return keyboard([[(t("button.back"), callback(name, str(page_number)))]])


def detail(data: dict, page_number: int):
    rows = []
    if data["removable"]:
        rows.append([(t("tm.remove"), callback("remove", str(data["user"].id)))])
    rows.append([(t("button.back"), callback("page", str(page_number)))])
    return keyboard(rows)


def preview(nonce: str, target_id: int):
    return keyboard(
        [
            [(t("tm.confirm"), callback("confirm", nonce))],
            [(t("button.back"), callback("view", str(target_id)))],
            [(t("button.cancel"), callback("cancel"))],
        ]
    )


def search(page_number: int):
    return keyboard(
        [
            [(t("button.back"), callback("page", str(page_number)))],
            [(t("button.cancel"), callback("cancel"))],
        ]
    )

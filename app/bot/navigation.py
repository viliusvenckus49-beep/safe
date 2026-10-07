"""Restore only the controls appropriate to an unfinished flow after input errors."""

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup

from app.bot import group_keyboards, keyboards, trusted_keyboards
from app.bot.states import (
    AdminAccessFlow,
    AdminRepFlow,
    InputFlow,
    RecoveryFlow,
    ReportFlow,
    RepVoteFlow,
    ScamAdminFlow,
)


async def error_navigation(
    state: FSMContext | None, *, private: bool
) -> InlineKeyboardMarkup | None:
    if state is None or not private:
        return None
    stage = await state.get_state()
    data = await state.get_data()
    report_stages = {
        item.state: name
        for item, name in (
            (ReportFlow.target, "target"),
            (ReportFlow.reason, "reason"),
            (ReportFlow.evidence, "evidence"),
            (ReportFlow.preview, "preview"),
        )
    }
    if stage in report_stages and data.get("nonce"):
        return keyboards.report(data["nonce"], report_stages[stage])
    if stage == AdminRepFlow.preview.state and data.get("nonce"):
        return keyboards.admin_rep_preview(data["nonce"])
    if stage in {
        AdminRepFlow.target.state,
        AdminRepFlow.amount.state,
        AdminRepFlow.reason.state,
    } and data.get("nonce"):
        return keyboards.admin_rep_navigation(data["nonce"], stage != AdminRepFlow.target.state)
    if stage in {ScamAdminFlow.input.state, ScamAdminFlow.preview.state} or "scam_page" in data:
        from app.bot.scam_admin import cb
        from app.i18n import t

        return keyboards.keyboard([[(t("button.back"), cb("page", str(data.get("scam_page", 0))))]])
    if stage == RepVoteFlow.comment.state:
        return keyboards.navigation()
    if stage == InputFlow.admin_target.state:
        return keyboards.navigation("admin")
    if stage == InputFlow.admin_reason.state:
        return keyboards.navigation("admin_target_back")
    if stage in {InputFlow.lookup.state, InputFlow.reputation.state}:
        return keyboards.navigation(
            data.get("lookup_return_name", "home"), data.get("lookup_return_value", "")
        )
    if stage == AdminAccessFlow.target.state:
        return keyboards.administrator_input()
    if stage == AdminAccessFlow.preview.state and data.get("access_nonce"):
        return keyboards.administrator_preview(data["access_nonce"])
    if "trusted_page" in data:
        return trusted_keyboards.search(int(data.get("trusted_page", 0)))
    if stage in {RecoveryFlow.invite.state, RecoveryFlow.preview.state} and data.get(
        "group_chat_id"
    ):
        return group_keyboards.recovery_input(data["group_chat_id"])
    return None

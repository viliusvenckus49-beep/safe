from aiogram.fsm.state import State, StatesGroup


class InputFlow(StatesGroup):
    lookup = State()
    reputation = State()
    admin_target = State()
    admin_reason = State()


class ReportFlow(StatesGroup):
    target = State()
    reason = State()
    evidence = State()
    preview = State()


class AdminRepFlow(StatesGroup):
    target = State()
    amount = State()
    reason = State()
    preview = State()


class AdminAccessFlow(StatesGroup):
    target = State()
    preview = State()


class RecoveryFlow(StatesGroup):
    invite = State()
    preview = State()


class TrustedAdminFlow(StatesGroup):
    search = State()
    preview = State()


class RepVoteFlow(StatesGroup):
    comment = State()


class ScamAdminFlow(StatesGroup):
    input = State()
    preview = State()

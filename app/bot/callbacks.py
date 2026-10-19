from aiogram.filters.callback_data import CallbackData


class Action(CallbackData, prefix="sc", sep="|"):
    name: str
    value: str = ""


class ReportStep(CallbackData, prefix="draft"):
    action: str
    nonce: str


class Moderation(CallbackData, prefix="mod"):
    action: str
    reference: str


class ReputationModeration(CallbackData, prefix="rpm"):
    action: str
    reference: str


class AdminRepStep(CallbackData, prefix="rpa"):
    action: str
    nonce: str


class Language(CallbackData, prefix="language"):
    lang: str


class AdminAccess(CallbackData, prefix="acl"):
    action: str
    value: str = ""


class AdminHelp(CallbackData, prefix="ah"):
    section: str = "menu"


class TrustedAdmin(CallbackData, prefix="ta"):
    action: str
    value: str = ""


class ScamAdmin(CallbackData, prefix="sa"):
    action: str
    value: str = ""


class LocalAction(CallbackData, prefix="lm2", sep="|"):
    action: str
    chat: int = 0
    value: str = ""


class AdsAction(CallbackData, prefix="ad3"):
    action: str
    campaign: int = 0
    value: str = ""


class AdsPreview(CallbackData, prefix="ab3"):
    source: str
    row: int
    column: int


class AdPublication(CallbackData, prefix="ap3"):
    action: str
    campaign: int
    value: str = ""


class PublishedAd(CallbackData, prefix="apb"):
    attempt: int
    row: int
    column: int


class AdGroupPublication(CallbackData, prefix="agp"):
    action: str
    campaign: int
    schedule: int
    value: str = ""


class GlobalAd(CallbackData, prefix="g33"):
    action: str
    campaign: int = 0
    value: str = ""


class CustomerAd(CallbackData, prefix="ac4"):
    action: str
    campaign: int = 0
    value: str = ""


class CustomerAdmin(CallbackData, prefix="aca4"):
    action: str
    campaign: int
    value: str = ""

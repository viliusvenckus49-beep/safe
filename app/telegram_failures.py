"""Stable, non-sensitive explanations for Telegram identity and moderation failures."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdentityLookup:
    user: Any = None
    code: str = "unavailable"
    retry_after: int = 0


def identity_failure(error: Exception) -> str:
    name = type(error).__name__
    if name == "UsernameInvalidError":
        return "invalid_username"
    if name == "UsernameNotOccupiedError":
        return "not_found"
    if name == "FloodWaitError":
        return "rate_limit"
    if name in {"AuthKeyUnregisteredError", "SessionRevokedError", "UserDeactivatedError"}:
        return "disconnected"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ValueError):
        return "not_user"
    return "unavailable"


def ban_failure(result: str, reason: str | None = None) -> str:
    description = (reason or "").lower()
    if result == "TelegramRetryAfter":
        return "rate_limit"
    if result == "TelegramForbiddenError" or any(
        code in description for code in ("not enough rights", "chat_admin_required", "rights")
    ):
        return "rights"
    if any(code in description for code in ("administrator", "user_admin_invalid", "chat owner")):
        return "admin"
    if any(code in description for code in ("not_participant", "id_invalid", "user not found")):
        return "unknown"
    if result == "GH_COMMAND_PENDING":
        return "verification"
    if result == "TimeoutError":
        return "timeout"
    if result == "API_FALSE":
        return "rejected"
    return "temporary"

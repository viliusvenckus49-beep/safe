"""Business failures carry stable codes, never user-facing text."""

import re


def safe_telegram_reason(message: str) -> str:
    """Bound Telegram diagnostics and redact credentials, URLs and control characters."""
    reason = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b", "[redacted]", message)
    reason = re.sub(r"https?://\S+", "[redacted URL]", reason)
    return re.sub(r"[\x00-\x1f\x7f]", " ", reason)[:256]


class DomainError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)

"""Stable city/region identifiers and bounded catalog inputs."""

import unicodedata
from datetime import UTC, datetime

from app.errors import DomainError

CITIES = ("vilnius", "kaunas", "klaipeda", "siauliai", "panevezys", "pajurys")
REGIONS = frozenset({"pajurys"})
PAGE_SIZE = 6
REVIEW_PAGE_SIZE = 2  # Two complete 1500-character comments fit Telegram's 4096 limit.
INPUT_TTL = 600
DRAFT_TTL = 3600


def normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip()


def bounded(value: str, minimum: int, maximum: int) -> str:
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(
        unicodedata.category(c).startswith("C") and c != "\n" for c in value
    ):
        raise DomainError("merchant_invalid")
    return value


def city(value: str) -> str:
    if value not in CITIES:
        raise DomainError("merchant_invalid")
    return value


def search(value: str) -> str:
    return normalized(bounded(value, 2, 64)).lstrip("@")


def grams(value: str) -> set[str]:
    return {value[i : i + 2] for i in range(len(value) - 1)}


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

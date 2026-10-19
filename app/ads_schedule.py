"""UTC fixed-duration slots and strict Europe/Vilnius civil-time resolution."""

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ads_content import validate_content
from app.errors import DomainError
from app.moderation_services import utc

INTERVALS = (1800, 3600, 7200, 10800, 14400, 21600, 28800, 43200, 86400)
VILNIUS = ZoneInfo("Europe/Vilnius")
LIVE = ("PENDING", "CLAIMED", "SENDING", "DEFERRED")
TERMINAL = ("SENT", "FAILED", "UNCERTAIN", "CANCELLED")


def local_instant(day: date, clock: str) -> datetime:
    """An ambiguous time needs an explicit +02:00/+03:00 offset; gaps always fail."""
    match = re.fullmatch(r"(\d{2}:\d{2})(?: ([+-]\d{2}:\d{2}))?", clock)
    if not match:
        raise DomainError("ap_time")
    try:
        naive = datetime.strptime(day.isoformat() + " " + match[1], "%Y-%m-%d %H:%M")
    except ValueError as error:
        raise DomainError("ap_time") from error
    candidates = {}
    for fold in (0, 1):
        aware = naive.replace(tzinfo=VILNIUS, fold=fold)
        instant = aware.astimezone(UTC)
        if instant.astimezone(VILNIUS).replace(tzinfo=None) == naive:
            candidates[instant] = aware.strftime("%z")
    offset = match[2].replace(":", "") if match[2] else None
    if offset:
        candidates = {instant: value for instant, value in candidates.items() if value == offset}
    if len(candidates) != 1:
        raise DomainError("ap_dst")
    return next(iter(candidates))


def local_datetime(value: str) -> datetime:
    try:
        day, clock = value.strip().split(" ", 1)
        return local_instant(date.fromisoformat(day), clock)
    except ValueError as error:
        raise DomainError("ap_time") from error


def slot(start: datetime, interval: int, instant: datetime, *, inclusive: bool = False) -> datetime:
    if type(interval) is not int or interval not in INTERVALS:
        raise DomainError("ap_interval")
    start, instant = utc(start), utc(instant)
    if instant < start or (inclusive and instant == start):
        return start
    elapsed = (instant - start).total_seconds()
    count = int(elapsed // interval)
    if not inclusive or elapsed != count * interval:
        count += 1
    return start + timedelta(seconds=count * interval)


def publishable(content: dict) -> dict:
    content = validate_content(content)
    if not content["text"].strip() and not content["media"]:
        raise DomainError("ap_content")
    return content


def parts_for(content: dict) -> list[str]:
    media = content["media"]
    if not media:
        return ["TEXT"]
    if len(media) == 1:
        return [media[0]["kind"].upper()]
    return ["ALBUM", "BUTTONS"] if content["buttons"] else ["ALBUM"]

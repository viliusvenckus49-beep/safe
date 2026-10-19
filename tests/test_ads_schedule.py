"""Civil time, fixed UTC intervals, native limits and complete localization."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.ads_schedule import INTERVALS, VILNIUS, local_datetime, local_instant, slot
from app.errors import DomainError
from app.i18n import CATALOGS


@pytest.mark.parametrize("seconds", INTERVALS)
def test_all_fixed_duration_intervals(seconds):
    start = datetime(2026, 3, 28, 12, tzinfo=UTC)
    assert slot(start, seconds, start, inclusive=True) == start
    assert slot(start, seconds, start) == start + timedelta(seconds=seconds)
    assert slot(start, seconds, start + timedelta(seconds=seconds * 20 + 1)) == start + timedelta(
        seconds=seconds * 21
    )


@pytest.mark.parametrize("seconds", [0, 60, 1801, 90000, True])
def test_unsupported_intervals_fail(seconds):
    with pytest.raises(DomainError):
        slot(datetime.now(UTC), seconds, datetime.now(UTC))


def test_spring_gap_and_autumn_ambiguity_require_explicit_resolution():
    with pytest.raises(DomainError, match="dst"):
        local_instant(date(2026, 3, 29), "03:30")
    with pytest.raises(DomainError, match="dst"):
        local_instant(date(2026, 10, 25), "03:30")
    early = local_instant(date(2026, 10, 25), "03:30 +03:00")
    late = local_instant(date(2026, 10, 25), "03:30 +02:00")
    assert late - early == timedelta(hours=1)
    with pytest.raises(DomainError):
        local_instant(date(2026, 3, 29), "03:30 +02:00")


@pytest.mark.parametrize("day", [date(2026, 3, 28), date(2026, 10, 24)])
def test_24_hours_never_becomes_23_or_25(day):
    start = local_instant(day, "12:00")
    following = slot(start, 86400, start)
    assert following - start == timedelta(hours=24)
    assert following.astimezone(VILNIUS).hour != 12


@pytest.mark.parametrize(
    "text",
    ["2026-99-01 12:00", "2026-10-20 25:00", "2026-10-20 12:00 +09:00", "now", "2026-10-20 1:00"],
)
def test_invalid_civil_dates_are_not_guessed(text):
    with pytest.raises(DomainError):
        local_datetime(text)


def test_utc_round_trip_and_locale_keys():
    value = local_datetime("2026-11-20 12:00")
    assert value == datetime(2026, 11, 20, 10, tzinfo=UTC)
    keys = {key for key in CATALOGS["lt"] if key.startswith("ap.")}
    assert keys and all(
        keys == {key for key in CATALOGS[lang] if key.startswith("ap.")} for lang in ("en", "ru")
    )

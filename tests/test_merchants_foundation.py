"""Phase A: city identifiers, safe inputs, localization and callback contracts."""

import pytest

from app.bot.callbacks import Merchant
from app.errors import DomainError
from app.locales.merchants import CATALOGS
from app.merchant_catalog import CITIES, REGIONS, bounded, grams, search


def test_six_cities_and_coastal_region_are_centralized():
    assert len(CITIES) == 6 and REGIONS == {"pajurys"}
    assert CATALOGS["lt"]["mc.klaipeda"] == "KLAIPĖDA"


def test_locales_and_compact_callback_contract():
    assert CATALOGS["lt"].keys() == CATALOGS["en"].keys() == CATALOGS["ru"].keys()
    for city in CITIES:
        assert len(Merchant(action="list", value=city, page=9999).pack().encode()) <= 64
    assert (
        len(Merchant(action="approve", entry=9223372036854775807, version=99999).pack().encode())
        <= 64
    )


def test_search_and_names_are_unicode_normalized_without_html_execution():
    assert search("@PARTNER") == "partner"
    assert search("  Urban Studio ") == "urban studio"
    assert grams("urban") == {"ur", "rb", "ba", "an"}
    assert bounded("<b>Studio</b>", 2, 64) == "<b>Studio</b>"  # Escaping is a rendering boundary.
    with pytest.raises(DomainError):
        bounded("bad\x00name", 2, 64)


@pytest.mark.parametrize(
    "text,accepted",
    [
        ("+Repair Studio", True),
        ("-Repair Studio", True),
        ("+REP @user", False),
        ("-rep @user", False),
        ("/ask 42", False),
        ("/rep 42", False),
        ("Studio", True),
    ],
)
def test_registration_words_are_not_confused_with_rep_commands(text, accepted):
    from types import SimpleNamespace
    from app.bot.merchants import ordinary_input

    assert ordinary_input(SimpleNamespace(text=text)) is accepted

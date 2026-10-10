from datetime import UTC, datetime
from string import Formatter
from types import SimpleNamespace

import pytest

from app import presentation as p
from app.bot import group_keyboards, group_presentation, keyboards
from app.i18n import use_language
from app.locales.presentation import CATALOGS


def test_all_presentation_catalogs_have_matching_keys_and_placeholders():
    reference = CATALOGS["lt"]
    for lang in ("en", "ru"):
        assert CATALOGS[lang].keys() == reference.keys()
    for key, template in reference.items():
        expected = {field for _, field, _, _ in Formatter().parse(template) if field}
        for lang in ("lt", "en", "ru"):
            translated = CATALOGS[lang][key]
            actual = {field for _, field, _, _ in Formatter().parse(translated) if field}
            assert actual == expected, (lang, key)
            values = {
                field: 5
                if field in ("score", "total", "count", "amount", "recipients")
                else "value"
                for field in actual
            }
            translated.format(**values)


@pytest.mark.parametrize(
    "lang,word", [("lt", "Reputacijos"), ("en", "Reputation"), ("ru", "репутации")]
)
def test_dynamic_cards_localize_and_escape_user_content(lang, word):
    user = SimpleNamespace(username=None, display_name="<script>&", telegram_id=None)
    now = datetime(2026, 10, 4, tzinfo=UTC)
    scam = SimpleNamespace(reason="<b>untrusted</b>", created_at=now, report_id=1)
    data = {"user": user, "scam": scam, "score": -3, "positive": 1, "negative": 4}
    with use_language(lang):
        profile = p.profile(data)
        assert word in profile
        assert "&lt;script&gt;&amp;" in profile
        assert "&lt;b&gt;untrusted&lt;/b&gt;" in profile
        assert "<script>" not in profile
        report = p.preview({"target": "<target>", "reason": "<reason>", "evidence": []})
        assert "&lt;target&gt;" in report and "&lt;reason&gt;" in report
        recovery = group_presentation.recovery_preview("<group>", "https://t.me/x?a=1&b=2", 5)
        assert "&lt;group&gt;" in recovery and "&amp;" in recovery
        assert (
            p.display_name(SimpleNamespace(username=None, display_name="Vartotojas"))
            == CATALOGS[lang]["p.user"]
        )


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_all_main_admin_and_group_buttons_localize(lang):
    with use_language(lang):
        menu = keyboards.home(admin=True)
        buttons = {button.text for row in menu.inline_keyboard for button in row}
        assert CATALOGS[lang]["button.lookup"] in buttons
        assert CATALOGS[lang]["button.language"] in buttons
        assert CATALOGS[lang]["button.close"] in buttons
        admin = {button.text for row in keyboards.admin().inline_keyboard for button in row}
        assert CATALOGS[lang]["button.pending"] in admin
        assert CATALOGS[lang]["button.audit"] in admin
        consent = {
            button.text
            for row in group_keyboards.consent(-100123).inline_keyboard
            for button in row
        }
        assert CATALOGS[lang]["button.subscribe"] in consent
        assert CATALOGS[lang]["button.unsubscribe"] in consent
        languages = keyboards.languages()
        assert {button.callback_data for row in languages.inline_keyboard for button in row} >= {
            "language:lt",
            "language:en",
            "language:ru",
        }


def test_recipient_locale_overrides_worker_context():
    with use_language("lt"):
        message = group_presentation.recovery_notification(
            "Group", "https://t.me/example", lang="ru"
        )
        assert "Восстановление группы" in message
        assert "Grupės atkūrimas" not in message

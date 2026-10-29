"""Merchant HTML fidelity and warning visibility share existing live status data."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from aiogram.methods import EditMessageText, SendMessage, SendPhoto
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.bot import merchant_views as views
from app.i18n import t, use_language
from app.services import Service

journey = telegram_journey


class CardHtml(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.stack, self.quotes, self.plain = [], [], ""
        self.feed(value)
        self.close()
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "a", "code", "blockquote"}
        if tag == "blockquote":
            assert tag not in self.stack
            self.quotes.append("")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, value):
        self.plain += value
        if "blockquote" in self.stack:
            assert "i" in self.stack
            self.quotes[-1] += value


def merchant(description):
    owner = SimpleNamespace(id=2, telegram_id=42, username="merchant", display_name="North <&>")
    entry = SimpleNamespace(
        name="North <&> Studio",
        owner=owner,
        city_code="vilnius",
        description=description,
        status="APPROVED",
        reason=None,
    )
    data = dict(
        user=owner, score=2, positive=5, negative=3, trusted=False, scam=None, unresolved_scam=None
    )
    return entry, data


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize(
    "description",
    [
        '<b>Raw HTML & "quotes"</b>\n\nSecond paragraph <script>text</script>',
        "😀" * 290 + "\n\n" + "🛡" * 290,
    ],
)
def test_every_description_view_quotes_the_full_escaped_text(lang, description):
    entry, data = merchant(description)
    with use_language(lang):
        public = views.profile(entry, data)
        for card in (
            public,
            views.fields(entry),
            views.private_entry(entry),
            views.editor_card({k: getattr(entry, k) for k in ("name", "description", "city_code")}),
        ):
            parsed = CardHtml(card)
            assert parsed.quotes[0] == description
            assert "<blockquote><i>" in card
            assert len(parsed.plain.encode("utf-16-le")) // 2 <= 4096
        assert entry.description == description
        assert "&lt;&amp;&gt;" in public
        assert t("mc.about") in public and t("mc.security_title") in public
        assert (
            public.index(t("mc.about"))
            < public.index(t("mc.rep_title"))
            < public.index(t("mc.security_title"))
        )
        assert f"<b>{t('mc.catalog_approved')}</b>" in public
        assert len(CardHtml(public).quotes) == 2
        assert f"<b>{t('mc.score')}:</b> <b>+2</b>" in public
        assert f"<b>{t('mc.positive')}:</b> <b>5</b>" in public
        assert f"<b>{t('mc.negative')}:</b> <b>3</b>" in public


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("status", ["ordinary", "trusted", "scam", "unresolved"])
def test_lookup_and_merchant_warnings_agree_with_scam_precedence(lang, status):
    entry, data = merchant("Lawful services\n\nA complete description.")
    data["trusted"] = status != "ordinary"
    data["trusted_source"] = "manual"
    if status in ("scam", "unresolved"):
        data["scam" if status == "scam" else "unresolved_scam"] = SimpleNamespace(
            id=7, report_id=None, reason="Confirmed record", created_at=datetime.now(UTC)
        )
    with use_language(lang):
        lookup, card = p.profile(data), views.profile(entry, data)
        assert (t("p.lookup_warning") in lookup) is (status != "trusted")
        assert (t("mc.disclaimer") in card) is (status != "trusted")
        assert (f"<b>{t('mc.trusted')}</b>" in card) is (status == "trusted")
        if status in ("scam", "unresolved"):
            assert "TRUSTED" not in lookup
        CardHtml(card)


def last_text(j):
    for call in reversed(j.transport.calls):
        if isinstance(call, (EditMessageText, SendMessage, SendPhoto)):
            return getattr(call, "text", None) or call.caption
    raise AssertionError("No Telegram result")


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_real_ask_warning_uses_current_trusted_scam_and_revocation(
    journey, database, settings, lang, monkeypatch
):
    clock = [1000.0]
    monkeypatch.setattr("app.bot.middleware.monotonic", lambda: clock[0])
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(1, lang)
        await core.observe(42, "merchant", "Merchant")
        await core.set_trusted(900, "42", True, "profile-design-trusted")
    await journey.send("/ask 42")
    assert t("p.lookup_warning", lang=lang) not in last_text(journey)
    assert "TRUSTED" in last_text(journey)
    async with database() as session:
        await Service(settings, session).add_scam(900, "42", "Confirmed isolated test record")
    clock[0] += 11
    await journey.send("/ask 42")
    assert t("p.lookup_warning", lang=lang) in last_text(journey)
    assert "TRUSTED" not in last_text(journey)
    async with database() as session:
        core = Service(settings, session)
        user = await core.repo.user_by_telegram(42)
        assert (await core.repo.trusted_designation(user.id)).active
        await core.remove_scam(900, "42", "Resolved isolated test")
        await core.set_trusted(900, "42", False, "profile-design-revoked")
    clock[0] += 11
    await journey.send("/ask 42")
    assert t("p.lookup_warning", lang=lang) in last_text(journey)

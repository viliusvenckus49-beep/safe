"""Public cards must distinguish status, provenance and identity without invented facts."""

from datetime import UTC, datetime
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.i18n import t, use_language
from app.services import Service

journey = telegram_journey


class ParsedText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = ""
        self.stack = []

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "code", "pre"}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        self.text += data


def parsed(value):
    parser = ParsedText()
    parser.feed(value)
    parser.close()
    assert not parser.stack
    return parser.text


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("status", ["clear", "manual", "top", "scam"])
def test_result_identity_and_status_are_factual(locale, known, status):
    user = SimpleNamespace(username="oscar", telegram_id=42 if known else None)
    record = SimpleNamespace(
        id=421, report_id=1, reason="Fraud <evidence>", created_at=datetime(2026, 10, 5, tzinfo=UTC)
    )
    data = dict(
        user=user,
        scam=record if status == "scam" else None,
        score=-4 if status == "scam" else 18,
        positive=1 if status == "scam" else 20,
        negative=5 if status == "scam" else 2,
        trusted=status != "clear",
        trusted_source=status,
        trusted_updated_at=datetime(2026, 10, 4, tzinfo=UTC),
    )
    with use_language(locale):
        result = p.profile(data)
        assert t("p.profile_title") in result
        assert f"[<code>{42 if known else t('p.id_unknown')}</code>]" in result
        assert "━━━━━━━━━━━━━━━" in result
        assert "＋REP" in result and "−REP" in result
        if status == "scam":
            assert "<b>−4</b>" in result
            assert t("p.lookup_scam_status") in result
            assert "SC-00421" in result and "2026-10-05" in result
            assert "Fraud &lt;evidence&gt;" in result
            assert "TRUSTED" not in result  # scam takes precedence even if input is inconsistent
        elif status == "clear":
            assert t("p.lookup_clear_status") in result
            assert t("p.no_scam") in result
        else:
            assert "TRUSTED" in result
            assert t("p.no_scam") not in result
            if status == "top":
                assert t("p.lookup_trusted_top_description") in result
                assert "2026-10-04" not in result  # no invented TOP confirmation date
            else:
                assert t("p.lookup_trusted_manual") in result
                assert "2026-10-04" in result
        if known:
            assert t("p.unknown_identity") not in result
            if status != "scam":
                assert t("p.lookup_identity_known") in result
        else:
            assert t("p.unknown_identity") in result
            assert t("p.lookup_identity_known") not in result
        if status == "clear":
            assert t("p.warning") in result
        else:
            assert t("p.warning") not in result
        assert len(parsed(result).encode("utf-16-le")) // 2 < 4096


def test_zero_score_and_missing_metadata_are_not_invented():
    data = dict(
        user=SimpleNamespace(username=None, display_name="<Name> & person", telegram_id=42),
        scam=None,
        score=0,
        positive=0,
        negative=0,
        trusted=True,
        trusted_source="manual",
    )
    result = p.profile(data)
    assert "<b>+0</b>" in result
    assert "&lt;Name&gt; &amp; person" in result
    assert "STATUSAS ATNAUJINTAS" not in result
    assert "#SC-" not in result
    parsed(result)


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
def test_long_public_cards_stay_within_telegram_limits(locale):
    user = SimpleNamespace(username=None, display_name="🚨" * 256 + "<untrusted>", telegram_id=42)
    reason = "😈" * 1500 + "<script>"
    record = SimpleNamespace(
        id=123, report_id=1, target=user, reason=reason, created_at=datetime.now(UTC)
    )
    with use_language(locale):
        for output in (
            p.profile(dict(user=user, scam=record, score=-1, positive=0, negative=1)),
            p.scams([record] * 5),
        ):
            assert len(parsed(output).encode("utf-16-le")) // 2 < 4096
            assert "…" in output
    assert record.reason == reason


async def test_manual_trusted_date_comes_from_database_and_top_has_no_manual_date(
    journey, database, settings
):
    async with database() as session:
        service = Service(settings, session)
        granted = await service.set_trusted(900, "42", True, "card-date")
        assert granted["trusted_updated_at"] is not None
        expected = f"{granted['trusted_updated_at']:%Y-%m-%d}"
        await service.set_top_visibility(900, "43", True, "Selected for TOP", "card-top")
        top = await service.profile("43")
        assert top["trusted_source"] == "top" and top["trusted_updated_at"] is None
    await journey.send("/ask 42", chat=-100)
    assert expected in journey.text()
    assert journey.transport.calls[-1].reply_markup is not None
    assert (
        journey.transport.calls[-1].reply_markup.inline_keyboard[-1][0].callback_data == "sc|close|"
    )


def test_simple_input_prompts_keep_their_existing_style():
    with use_language("lt"):
        assert (
            p.text("target")
            == "🔎 <b>Patikrinti vartotoją</b>\n\nĮvesk @vartotoją arba skaitinį Telegram ID."
        )
        for key in ("target", "admin_target", "admin_reason", "reason_invalid", "cooldown"):
            assert "━━━━━━━━" not in p.text(key)


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
def test_ids_in_people_lists_but_not_top(locale):
    known = SimpleNamespace(
        id=999, username="person", display_name="Person", telegram_id=8803241151
    )
    unknown = SimpleNamespace(id=998, username="unknown", display_name="Unknown", telegram_id=None)
    records = [
        SimpleNamespace(target=user, report_id=None, reason="", created_at=datetime.now(UTC))
        for user in (known, unknown)
    ]
    with use_language(locale):
        for output in (p.scams(records), p.users([known, unknown])):
            assert "8803241151" in output
            assert t("p.id_unknown") in output
            assert "998" not in output  # a database primary key is never a Telegram ID
        top = p.leaderboard([dict(user=known, score=1), dict(user=unknown, score=0)])
        assert "8803241151" not in top
        assert t("p.id_unknown") not in top

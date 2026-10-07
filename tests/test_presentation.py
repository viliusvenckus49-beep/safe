from types import SimpleNamespace

from app import presentation as p


def test_action_style_uses_telegram_identity_and_lithuanian():
    user = SimpleNamespace(username="typicalsterling", telegram_id=8803241151, display_name="")
    assert (
        p.scam_action(user, True)
        == "⛔️ <b>@typicalsterling</b> [<code>8803241151</code>] įtrauktas į SCAM registrą."
    )
    assert "pašalintas" in p.scam_action(user, False)
    assert "skirtas +REP" in p.reputation_action(user, 1)
    assert "skirtas -REP" in p.reputation_action(user, -1)
    assert "patvirtintas" in p.moderation_action(user, "SC-2026-000123", True)
    assert "SC-2026-000123" in p.moderation_action(user, "SC-2026-000123", True)
    assert "atmestas" in p.moderation_action(user, "SC-2026-000123", False)


def test_unknown_identity_escaped_without_invented_id():
    user = SimpleNamespace(username=None, telegram_id=None, display_name="<unsafe>", id=123)
    assert p.identity(user) == "<b>&lt;unsafe&gt;</b> [<code>ID nežinomas</code>]"


def test_legacy_retry_and_unverified_reputation_review_are_safe():
    from datetime import UTC, datetime

    user = SimpleNamespace(username="unknown_name", telegram_id=None, display_name="")
    data = dict(user=user, score=1, positive=1, negative=0, scam=None, reputation_request=None)
    assert "išsaugotas" in p.reputation_pending(data)
    request = SimpleNamespace(
        reference="RP-2026-000001",
        receiver=user,
        giver=user,
        value=1,
        status="PENDING",
        created_at=datetime.now(UTC),
    )
    assert "savininkas gali pasikeisti" in p.reputation_review(request)
    assert "Laukia sprendimo" in p.reputation_review(request)


def test_leaderboard_fixed_columns_and_ten_rows():
    from html import unescape

    rows = [
        {"user": SimpleNamespace(display_name="", username=name), "score": score}
        for name, score in [("notoriouslyreborn", 1), ("short", 120)]
    ]
    output = p.leaderboard(rows)
    lines = unescape(output.split("<pre>")[1].split("</pre>")[0]).splitlines()
    assert len(lines) == 10
    assert len({line.rindex("┃") for line in lines}) == 1
    assert len({len(line) for line in lines}) == 1
    assert lines[0].startswith("01 ┃") and lines[-1].startswith("10 ┃")
    assert p.submitted("SC-2026-000001") == "📨 <b>Pranešimas pateiktas</b>"


def test_table_long_name_is_bounded_and_html_escaped():
    assert p._table_width(p._table_name("A" * 100)) == 18
    assert p._table_name("A" * 100).endswith("…")
    user = SimpleNamespace(display_name="<b>Name</b>", username="example")
    assert "&lt;b&gt;Name&lt;/b&gt;" in p.leaderboard([{"user": user, "score": 1}])


def test_manual_scam_confirmation_and_report_reason_preservation():
    from datetime import UTC, datetime

    from app.i18n import t, use_language

    user = SimpleNamespace(username="example", telegram_id=None)
    record = SimpleNamespace(
        target=user, report_id=None, reason="proofs <private note>", created_at=datetime.now(UTC)
    )
    data = dict(user=user, scam=record, score=0, positive=0, negative=0)
    for lang in ("lt", "en", "ru"):
        with use_language(lang):
            for output in (p.profile(data), p.scams([record])):
                assert t("p.scam_admin_confirmation") in output
                assert "proofs" not in output
            assert t("p.unknown_identity") not in p.scams([record])
            assert t("p.unknown_identity") in p.profile(data)
            record.report_id = 1
            assert "proofs &lt;private note&gt;" in p.profile(data)
            assert "proofs &lt;private note&gt;" in p.scams([record])
            assert t("p.scam_admin_confirmation") not in p.profile(data)
            record.report_id = None
    assert record.reason == "proofs <private note>"


def test_symbol_only_top_name_falls_back_to_username():
    from app.i18n import use_language

    for lang in ("lt", "en", "ru"):
        with use_language(lang):
            user = SimpleNamespace(display_name="™😀\u200b", username="cart3lis")
            result = p.leaderboard([{"user": user, "score": 0}])
            assert "@cart3lis" in result
            assert p.t("p.user") not in result
            assert p._table_name("™", fallback="@example") == "@example"
            assert p._table_name("™") == p.t("p.user")

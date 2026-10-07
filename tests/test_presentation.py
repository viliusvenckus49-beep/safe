from types import SimpleNamespace

import pytest

from app import presentation as p
from app.i18n import t, use_language


def test_action_style_uses_telegram_identity_and_lithuanian():
    user = SimpleNamespace(username="typicalsterling", telegram_id=8803241151, display_name="")
    added = p.scam_action(user, True)
    assert "𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗" in added
    assert "@typicalsterling" in added and "8803241151" in added
    assert t("p.ban_unconfirmed") in added
    assert "𝗕𝗟𝗢𝗖𝗞𝗘𝗗" not in added
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


def test_leaderboard_has_ten_rows_without_space_alignment_or_ids():
    rows = [
        {"user": SimpleNamespace(display_name="", username=name), "score": score}
        for name, score in [("notoriouslyreborn", 1), ("short", 120)]
    ]
    output = p.leaderboard(rows)
    lines = output.split("\n\n")[-1].splitlines()
    assert len(lines) == 10
    assert lines[0] == "1. @notoriouslyreborn · <b>+1</b>"
    assert lines[1] == "2. @short · <b>+120</b>"
    assert lines[-1] == "10. — · <b>—</b>"
    assert "<pre>" not in output and "  " not in output
    assert p.submitted("SC-2026-000001") == "📨 <b>Pranešimas pateiktas</b>"


def test_top_long_name_is_bounded_and_html_escaped():
    long_user = SimpleNamespace(display_name="A" * 1000, username="example")
    assert "A" * 79 + "…" in p.leaderboard([{"user": long_user, "score": 1}])
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


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_trusted_receipt_preserves_identity_and_localizes_body(lang):
    user = SimpleNamespace(username="ordinary_username", telegram_id=78654739, display_name="")
    with use_language(lang):
        result = p.trusted_granted(user)
        assert result.startswith("✅ 𝗧𝗥𝗨𝗦𝗧𝗘𝗗 • 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗")
        assert "👤 @ordinary_username\n🆔 <code>78654739</code>" in result
        assert result.endswith(t("p.brand"))
        assert t("p.receipt_identity_unknown") not in result
        user.username = None
        user.display_name = "Alice <Bob> & 😀"
        assert "Alice &lt;Bob&gt; &amp; 😀" in p.trusted_granted(user)
        user.telegram_id = None
        unknown = p.trusted_granted(user)
        assert "𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗" not in unknown
        assert "𝗨𝗡𝗞𝗡𝗢𝗪𝗡" in unknown
        assert t("p.receipt_identity_unknown") in unknown


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize(
    "groups,succeeded,failed,pending,key,blocked",
    [
        (5, 5, 0, 0, "p.ban_complete", True),
        (5, 3, 2, 2, "p.ban_partial", False),
        (5, 0, 5, 5, "p.ban_pending", False),
        (5, 0, 0, 5, "p.ban_pending", False),
        (0, 0, 0, 0, "p.ban_no_groups", False),
    ],
)
def test_scam_receipt_reports_only_actual_ban_results(
    lang, groups, succeeded, failed, pending, key, blocked
):
    user = SimpleNamespace(username="person_name", telegram_id=7681768804, display_name="")
    with use_language(lang):
        result = p.scam_registered(
            user, groups=groups, succeeded=succeeded, failed=failed, pending=pending
        )
        assert "@person_name" in result and "7681768804" in result
        assert ("𝗕𝗟𝗢𝗖𝗞𝗘𝗗" in result) is blocked
        assert ("𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗" in result) is not blocked
        assert (
            t(
                "p.ban_status" if groups else key,
                groups=groups,
                succeeded=succeeded,
                banned=succeeded,
                already_banned=0,
                pending=pending,
            )
            in result
        )
        assert result.endswith(t("p.brand"))


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_scam_receipt_distinguishes_unknown_id_from_rejected_bans(lang):
    user = SimpleNamespace(username="not_yet_identified", telegram_id=None, display_name="")
    with use_language(lang):
        unknown = p.scam_registered(user, groups=5, succeeded=0, failed=5, pending=5)
        assert "𝗨𝗡𝗞𝗡𝗢𝗪𝗡" in unknown and t("p.ban_inactive") in unknown
        assert "𝗕𝗟𝗢𝗖𝗞𝗘𝗗" not in unknown
        user.telegram_id = 123456789
        rejected = p.scam_registered(user, groups=5, succeeded=0, failed=5, pending=5)
        assert t("p.ban_inactive") not in rejected
        assert "Queued  5" in rejected


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
def test_requested_block_status_style_uses_actual_counts_and_numeric_fallback(lang):
    user = SimpleNamespace(username=None, telegram_id=8933903466, display_name="Vartotojas")
    with use_language(lang):
        receipt = p.scam_registered(
            user,
            groups=5,
            succeeded=2,
            already_banned=2,
            failed=3,
            pending=3,
        )
    assert "👤 User 8933903466" in receipt
    assert "🚷 𝗕𝗟𝗢𝗖𝗞 𝗦𝗧𝗔𝗧𝗨𝗦" in receipt
    assert "Banned  0/5  •  Already banned  2  •  Queued  3" in receipt
    assert "𝗥𝗘𝗚𝗜𝗦𝗧𝗘𝗥𝗘𝗗" in receipt and "𝗕𝗟𝗢𝗖𝗞𝗘𝗗" not in receipt

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
        assert tag in {"b", "code", "pre", "blockquote", "i"}
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack.pop() == tag

    def handle_data(self, data):
        if (
            "blockquote" in self.stack
            and data.strip()
            and not any(symbol in data for symbol in ("⭐", "👍", "👎"))
        ):
            assert "i" in self.stack
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
        assert f"🆔 ID: <code>{42 if known else t('p.lookup_id_unknown')}</code>" in result
        assert "⭐" in result and "👍" in result and "👎" in result
        assert t("p.lookup_status") in result and t("p.lookup_identity_heading") in result
        from app.bot import keyboards as kb
        from app.bot.callbacks import Action

        buttons = kb.check_result(user.telegram_id).inline_keyboard
        assert len(buttons) == 3 and all(len(row) == 1 for row in buttons)
        assert buttons[0][0].text == t("button.check_profile")
        assert buttons[1][0].text == t("button.lookup_other")
        assert Action.unpack(buttons[1][0].callback_data).name == "lookup"
        assert buttons[2][0].text == t("button.check_home")
        assert Action.unpack(buttons[0][0].callback_data).value == ("42" if known else "unknown")
        if status == "scam":
            assert "−4" in result
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
                assert "2026-10-04" not in result
        if known:
            assert t("p.lookup_identity_unknown") not in result
            assert t("p.lookup_identity_known") in result
        else:
            assert t("p.lookup_identity_unknown") in result
            assert t("p.lookup_identity_known") not in result
        assert result.endswith(t("p.lookup_warning"))
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
    assert "+0" in result
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
    assert expected not in journey.text()
    assert journey.transport.calls[-1].reply_markup is not None
    assert (
        journey.transport.calls[-1].reply_markup.inline_keyboard[-1][0].callback_data
        == "sc|home|receipt"
    )


def test_simple_input_prompts_keep_their_existing_style():
    with use_language("lt"):
        assert (
            p.text("target")
            == "◈ <b>Patikrinti vartotoją</b>\n\nĮvesk @vartotoją arba skaitinį Telegram ID."
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


def test_lithuanian_check_matches_requested_design_and_html():
    user = SimpleNamespace(id=8, username="owned1111", telegram_id=6575329720)
    with use_language("lt"):
        result = p.profile(dict(user=user, scam=None, score=0, positive=0, negative=0))
    assert result == (
        "🛡 𝑪𝑹𝑰𝑴𝑺𝑶𝑵 𝑺𝑨𝑭𝑬𝑪𝑯𝑬𝑪𝑲™\n\n"
        "🔎 <b>REDSAFE PATIKRA</b>\n\n"
        "👤 @owned1111\n🆔 ID: <code>6575329720</code>\n\n"
        "⭐ <b>Reputacijos statistika</b>\n"
        "<blockquote>⭐ Bendra reputacija: +0\n👍 Teigiami įvertinimai: 0\n👎 Neigiami įvertinimai: 0</blockquote>\n\n"
        "🛡 <b>Saugumo statusas</b>\n\n☑ <b>SCAM ĮRAŠŲ NERASTA</b>\n"
        "<blockquote><i>Patvirtintų sukčiavimo įrašų duomenų bazėje nėra.</i></blockquote>\n\n"
        "🔐 <b>Tapatybės patvirtinimas</b>\n\n☑ <b>TELEGRAM ID SUSIETAS</b>\n"
        "<blockquote><i>Paskyra identifikuota pagal unikalų Telegram ID, ne vien vartotojo vardą.</i></blockquote>\n\n"
        "⚠️ <i>Patikros rezultatas</i> <b>negarantuoja vartotojo patikimumo.</b>"
    )
    assert result.count("<blockquote><i>") == result.count("</i></blockquote>") == 2
    assert result.count("<blockquote>") == 3
    parsed(result)


@pytest.mark.asyncio
async def test_checked_profile_keeps_numeric_target_after_username_change(
    journey, database, settings
):
    from aiogram.methods import SendMessage

    from app.bot import keyboards as kb

    async with database() as session:
        await Service(settings, session).observe(42, "checked_person", "Checked person")
    await journey.send("/ask @checked_person")
    result = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage)
    )
    assert [[button.text for button in row] for row in result.reply_markup.inline_keyboard] == [
        ["🪪 REDSAFE PROFILIS"],
        ["ᴛɪᴋʀɪɴᴛɪ ᴋɪᴛᴀ"],
        ["‹ Pagrindinis meniu"],
    ]
    profile_callback = result.reply_markup.inline_keyboard[0][0].callback_data
    assert profile_callback == kb.action("profile", "42")
    assert result.reply_markup.inline_keyboard[2][0].callback_data == kb.action("home", "receipt")
    async with database() as session:
        core = Service(settings, session)
        await core.observe(42, "changed_person", "Checked person")
        await core.observe(43, "checked_person", "Different person")
    await journey.click(profile_callback)
    profile = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage)
    )
    assert "REDSAFE PROFILIS" in profile.text and "🆔 ID: 42" in profile.text
    assert "@changed_person" in profile.text and "Different person" not in profile.text
    # The unchanged main-menu callback with an empty value still opens the actor's own profile.
    await journey.click(kb.action("profile"))
    own = next(call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage))
    assert "🆔 ID: 1" in own.text


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["unknown", "@unknown_name", "u:1", "-123", "0"])
async def test_unknown_or_invalid_profile_target_never_opens_another_user(journey, target):
    from aiogram.methods import AnswerCallbackQuery, SendMessage

    from app.bot import keyboards as kb

    await journey.send("/ask @unknown_name")
    result = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, SendMessage)
    )
    assert result.reply_markup.inline_keyboard[0][0].callback_data == kb.action(
        "profile", "unknown"
    )
    count = sum(isinstance(call, SendMessage) for call in journey.transport.calls)
    await journey.click(kb.action("profile", target))
    assert sum(isinstance(call, SendMessage) for call in journey.transport.calls) == count
    notice = next(
        call for call in reversed(journey.transport.calls) if isinstance(call, AnswerCallbackQuery)
    )
    assert notice.show_alert is True
    assert notice.text == t("p.lookup_profile_unavailable")


@pytest.mark.parametrize("locale", ["lt", "en", "ru"])
@pytest.mark.parametrize("source", [None, "manual", "role"])
def test_reference_spacing_and_quote_boundaries(locale, source):
    with use_language(locale):
        data = dict(
            user=SimpleNamespace(username="owned11111", telegram_id=6961937011),
            scam=None,
            score=0,
            positive=0,
            negative=0,
            trusted=source is not None,
            trusted_source=source,
            role="moderator" if source == "role" else None,
            trusted_updated_at=datetime(2026, 10, 4, tzinfo=UTC),
            identity_lookup=SimpleNamespace(code="not_found"),
        )
        card = p.profile(data)
        assert card.count("<blockquote>") == card.count("</blockquote>") == 3
        reputation = card.split("<blockquote>", 1)[1].split("</blockquote>", 1)[0]
        assert len(reputation.splitlines()) == 3 and "<i>" not in reputation
        assert "\n<blockquote>⭐" in card and "\n\n<blockquote>⭐" not in card
        status = t("p.trusted_status" if source else "p.lookup_clear_status")
        assert status + "\n<blockquote><i>" in card
        assert t("p.lookup_identity_heading") + "\n\n" in card
        assert "</b>\n<blockquote><i>" in t("p.lookup_identity_known")
        assert t("p.lookup_trusted_role") not in card
        assert t("p.role_moderator") not in card
        assert "2026-10-04" not in card
        assert card.endswith(t("p.lookup_warning"))
        parsed(card)

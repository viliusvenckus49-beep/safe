from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_telegram import journey as telegram_journey

from app import presentation as p
from app.bot.callbacks import ScamAdmin
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import CATALOGS, t, use_language
from app.models import AuditEvent
from app.services import Service
from app.telegram_failures import IdentityLookup, ban_failure, identity_failure

journey = telegram_journey


@pytest.mark.parametrize(
    "name,expected",
    [
        ("UsernameInvalidError", "invalid_username"),
        ("UsernameNotOccupiedError", "not_found"),
        ("FloodWaitError", "rate_limit"),
        ("SessionRevokedError", "disconnected"),
    ],
)
def test_identity_failures_do_not_claim_group_help_has_no_data(name, expected):
    error = type(name, (Exception,), {})("private token and session details")
    assert identity_failure(error) == expected
    for language in ("lt", "en", "ru"):
        with use_language(language):
            text = p.identity_lookup_note(IdentityLookup(code=expected, retry_after=25))
            assert "group help" not in text.lower() and "private token" not in text
            assert t("diagnostic.identity_" + expected, seconds=25) in text


@pytest.mark.parametrize(
    "result,reason,code",
    [
        ("TelegramBadRequest", "not enough rights: administrator required", "rights"),
        ("TelegramBadRequest", "CHAT_ADMIN_REQUIRED", "rights"),
        ("TelegramBadRequest", "USER_ADMIN_INVALID", "admin"),
        ("TelegramBadRequest", "PARTICIPANT_ID_INVALID", "unknown"),
        ("TelegramRetryAfter", None, "rate_limit"),
        ("TimeoutError", None, "timeout"),
        ("API_FALSE", None, "rejected"),
    ],
)
def test_ban_cause_classification_avoids_admin_rights_confusion(result, reason, code):
    assert ban_failure(result, reason) == code


@pytest.mark.asyncio
async def test_private_diagnostics_show_real_current_groups_and_attempt_causes(database, settings):
    async with database() as session:
        core, groups = Service(settings, session), GroupService(settings, session)
        for chat, title in ((-1000, "First <group>"), (-1001, "Second")):
            await groups.register_group(900, chat, title, True)
        record = await core.add_scam(900, "42")
        jobs = await groups.claim_bans(scam_record_id=record.id)
        for job in jobs:
            if job.chat_id == -1000:
                await groups.finish_ban(
                    job.id,
                    False,
                    "TelegramBadRequest",
                    permanent=True,
                    reason="not enough rights token=PRIVATE",
                )
            else:
                await groups.finish_ban(job.id, True, "ALREADY_BANNED")
        await groups.finish_ban(jobs[0].id, False, "API_FALSE")
        rows = await groups.ban_diagnostics(900, record.id)
        failed = next(row for row in rows if row["chat_id"] == -1000)
        assert failed["cause"] == "rights" and failed["paused"]
        attempts = list(
            (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.action == "scam_ban_attempt")
                )
            ).all()
        )
        assert len(attempts) == 2
        assert "PRIVATE" not in str([a.details for a in attempts])
        for language in ("lt", "en", "ru"):
            with use_language(language):
                text = p.ban_diagnostics(record.target, rows)
                assert "First &lt;group&gt;" in text
                assert t("diagnostic.ban_rights") in text
                assert t("diagnostic.ban_already") in text
                assert "PRIVATE" not in text
        with pytest.raises(DomainError):
            await groups.ban_diagnostics(1, record.id)


@pytest.mark.asyncio
async def test_stale_completion_creates_no_false_attempt_record(database, settings):
    async with database() as session:
        core, groups = Service(settings, session), GroupService(settings, session)
        await groups.register_group(900, -1000, "Removed", True)
        await core.add_scam(900, "42")
        job = (await groups.claim_bans())[0]
        job.status = "OBSOLETE"
        await session.commit()
        await groups.finish_ban(job.id, True, "BANNED")
        assert not (
            await session.scalars(select(AuditEvent).where(AuditEvent.action == "scam_ban_attempt"))
        ).all()


@pytest.mark.asyncio
async def test_details_callback_and_status_require_private_admin(journey, database, settings):
    async with database() as session:
        record = await Service(settings, session).add_scam(900, "42")
        record_id = record.id
    callback = ScamAdmin(action="details", value=str(record_id)).pack()
    await journey.click(callback, actor=1)
    await journey.click(callback, actor=900, chat=-123)
    assert t("diagnostic.ban_title") not in journey.text()
    await journey.click(callback, actor=900)
    assert t("diagnostic.ban_title") in journey.text()
    await journey.send("/status", actor=1)
    await journey.send("/status", actor=900, chat=-123)
    assert "<b>BOTO BŪKLĖ</b>" not in journey.text()
    await journey.send("/status", actor=900)
    assert "<b>BOTO BŪKLĖ</b>" in journey.text()


def test_diagnostic_catalogs_have_identical_keys_and_bounded_output():
    keys = {key for key in CATALOGS["lt"] if key.startswith("diagnostic.")}
    assert all(
        {key for key in CATALOGS[lang] if key.startswith("diagnostic.")} == keys
        for lang in ("en", "ru")
    )
    rows = [
        dict(
            title="🛡" * 200,
            chat_id=-1000,
            status="FAILED",
            cause="rights",
            result="TelegramBadRequest",
            paused=True,
        )
    ] * 40
    for lang in ("lt", "en", "ru"):
        with use_language(lang):
            text = p.ban_diagnostics(
                SimpleNamespace(username="someone", telegram_id=42, display_name="Original"), rows
            )
            assert len(text.encode("utf-16-le")) // 2 < 4096

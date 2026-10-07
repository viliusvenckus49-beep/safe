"""Failed presence bans privately alert the owner once, without public group spam."""

import pytest
from aiogram.exceptions import TelegramBadRequest
from sqlalchemy import func, select
from test_scam_ban_flow import BanBot, prepare

from app.bot.group_runtime import process_scam_bans
from app.bot.moderation_notices import notify_ban_failure, reason_key
from app.group_services import GroupService
from app.models import AuditEvent
from app.services import Service


class AlertBot(BanBot):
    def __init__(self, failures=None):
        super().__init__(failures)
        self.notices = []

    async def send_message(self, chat_id, text):
        self.notices.append((chat_id, text))
        return True


@pytest.mark.asyncio
async def test_presence_failure_alert_is_private_and_persists_across_sessions(database, settings):
    record_id = await prepare(database, settings, groups=1)
    async with database() as session:
        await Service(settings, session).set_language(900, "en")
    bot = AlertBot({-1000: TelegramBadRequest})
    await process_scam_bans(bot, settings, database, record_id)
    assert bot.notices == []  # Unseen users awaiting preemptive bans do not spam the owner.
    for _ in range(3):
        async with database() as session:
            await GroupService(settings, session).check_member(-1000, 22, fresh_join=True)
        await process_scam_bans(bot, settings, database, record_id)
    assert len(bot.notices) == 1
    recipient, text = bot.notices[0]
    assert recipient == 900 and "Could not block" in text and "22" in text
    assert "Group 0" in text
    async with database() as session:
        notice = await session.scalar(
            select(AuditEvent).where(AuditEvent.action == "scam_ban_alert")
        )
        assert notice.details["status"] == "SENT"
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_ban_alert")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_successful_ban_does_not_send_failure_alert(database, settings):
    record_id = await prepare(database, settings, groups=1)
    async with database() as session:
        await GroupService(settings, session).check_member(-1000, 22)
    bot = AlertBot()
    await process_scam_bans(bot, settings, database, record_id)
    async with database() as session:
        await notify_ban_failure(bot, GroupService(settings, session), -1000, 22, present=True)
    assert bot.notices == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_update_failure_notice_escapes_identity_and_is_deduplicated(database, settings, lang):
    await prepare(database, settings, groups=1)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
        await Service(settings, session).observe(22, None, "Alice <admin> & Bob")
    bot = AlertBot()
    for _ in range(2):
        async with database() as session:
            await notify_ban_failure(bot, GroupService(settings, session), -1000, 22, present=True)
    assert len(bot.notices) == 1
    assert "Alice &lt;admin&gt; &amp; Bob" in bot.notices[0][1]


@pytest.mark.parametrize(
    "result,reason,key",
    [
        ("TelegramBadRequest", "user is an administrator", "admin"),
        ("TelegramForbiddenError", None, "rights"),
        ("TelegramBadRequest", "PARTICIPANT_ID_INVALID", "unknown"),
        ("TelegramRetryAfter", None, "rate_limit"),
        ("UpdateError", None, "temporary"),
    ],
)
def test_private_reasons_are_short_localization_keys(result, reason, key):
    assert reason_key(result, reason) == "ban_alert." + key

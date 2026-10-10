"""Registration broadcasts exercise routing, durable delivery and real group eligibility."""

import asyncio
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import SendMessage
from sqlalchemy import select
from test_integration_contracts import postgres_contract as postgres_fixture
from test_telegram import journey as telegram_journey

from app.admin_services import AdminService
from app.bot import scam_broadcasts
from app.bot.group_runtime import process_group_jobs
from app.bot.scam_broadcasts import ACTION, process_scam_announcements, queue_scam_announcements
from app.bot.scam_notices import registered_scam_text
from app.group_services import GroupService
from app.i18n import CATALOGS
from app.models import AuditEvent, ManagedGroup, ScamRecord, now
from app.services import Service

journey = telegram_journey
postgres_contract = postgres_fixture
GROUPS = [-1003976343267, -1004430959898, -1004413023527]


class NoticeBot:
    def __init__(self, failures=None):
        self.calls = []
        self.failures = failures or {}

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append((chat_id, text, kwargs))
        error = self.failures.get(chat_id)
        if error:
            options = {"retry_after": 90} if error is TelegramRetryAfter else {}
            raise error(method=SendMessage(chat_id=chat_id, text=text), message="denied", **options)
        return SimpleNamespace(message_id=len(self.calls))


async def prepare(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        for chat_id in GROUPS:
            await groups.register_group(900, chat_id, "Group", True)


async def notices(database):
    async with database() as session:
        return list(await session.scalars(select(AuditEvent).where(AuditEvent.action == ACTION)))


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
@pytest.mark.parametrize("target", ["42", "@example"])
async def test_registration_sends_once_to_all_groups_with_existing_refresh(
    journey, database, settings, lang, target
):
    await prepare(database, settings)
    async with database() as session:
        await Service(settings, session).set_language(900, lang)
    await journey.send("/add_sc " + target, actor=900)
    async with database() as session:
        record = (await session.scalars(select(ScamRecord))).one()
        record_id = record.id
    bot = NoticeBot()
    assert await process_scam_announcements(bot, settings, database) == 3
    assert {call[0] for call in bot.calls} == set(GROUPS)
    for _, text, kwargs in bot.calls:
        assert "𝗦𝗖𝗔𝗠" in text and ("42" in text if target == "42" else "@example" in text)
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        assert button.text == CATALOGS[lang]["sm.refresh"]
        assert button.callback_data == f"sa:retry_receipt:{record_id}"
    await queue_scam_announcements(settings, database, record_id)
    assert await process_scam_announcements(bot, settings, database) == 0
    assert len(bot.calls) == 3
    assert {row.details["status"] for row in await notices(database)} == {"SENT"}


async def test_source_group_keeps_one_receipt_other_groups_get_broadcast(
    journey, database, settings
):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900, chat=GROUPS[0])
    bot = NoticeBot()
    await process_scam_announcements(bot, settings, database)
    assert {call[0] for call in bot.calls} == set(GROUPS[1:])
    assert len([c for c in journey.transport.calls if isinstance(c, SendMessage)]) == 1
    assert [n.details["status"] for n in await notices(database)].count("SOURCE") == 1


async def test_admin_target_wizard_and_granted_admin_also_broadcast(journey, database, settings):
    await prepare(database, settings)
    async with database() as session:
        await AdminService(settings, session).change(900, "42", True, "a" * 32)
    await journey.send("/add_sc", actor=42)
    await journey.send("@example", actor=42)
    bot = NoticeBot()
    await process_scam_announcements(bot, settings, database)
    assert len(bot.calls) == 3
    assert all(n.actor_id == 42 for n in await notices(database))


async def test_regular_user_cannot_enqueue_public_scam_notices(journey, database, settings):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=1)
    assert await notices(database) == []


async def test_disabled_unapproved_removed_groups_and_removed_scam_never_receive_notice(
    journey, database, settings
):
    await prepare(database, settings)
    async with database() as session:
        (await session.get(ManagedGroup, GROUPS[0])).enabled = False
        (await session.get(ManagedGroup, GROUPS[1])).approved = False
        await session.commit()
    await journey.send("/add_sc @example", actor=900)
    assert [n.details["chat_id"] for n in await notices(database)] == [GROUPS[2]]
    async with database() as session:
        await GroupService(settings, session).remove_group(900, GROUPS[2])
    bot = NoticeBot()
    await process_scam_announcements(bot, settings, database)
    assert bot.calls == []
    assert (await notices(database))[0].details["status"] == "CANCELLED"
    async with database() as session:
        await GroupService(settings, session).register_group(900, -1009999999999, "New group", True)
    await journey.send("/add_sc @other_user", actor=900)
    async with database() as session:
        await Service(settings, session).remove_scam(900, "@other_user", "Record reviewed")
    await process_scam_announcements(bot, settings, database)
    assert bot.calls == []


async def test_failures_do_not_block_other_groups_and_flood_wait_is_respected(
    journey, database, settings
):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900)
    bot = NoticeBot({GROUPS[0]: TelegramForbiddenError, GROUPS[1]: TelegramRetryAfter})
    await process_scam_announcements(bot, settings, database)
    states = {n.details["chat_id"]: n.details for n in await notices(database)}
    assert states[GROUPS[0]]["status"] == "FAILED"
    assert states[GROUPS[1]]["status"] == "RETRY"
    assert states[GROUPS[1]]["next_attempt_at"] >= int(now().timestamp()) + 89
    assert states[GROUPS[2]]["status"] == "SENT"
    assert await process_scam_announcements(bot, settings, database) == 0
    assert len(bot.calls) == 3
    async with database() as session:
        event = await session.get(
            AuditEvent,
            next(n.id for n in await notices(database) if n.details["chat_id"] == GROUPS[1]),
        )
        event.details = {**event.details, "next_attempt_at": 0}
        await session.commit()
    bot.failures = {}
    assert await process_scam_announcements(bot, settings, database) == 1
    assert len(bot.calls) == 4


async def test_abandoned_claim_is_recovered_after_restart(journey, database, settings):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900)
    async with database() as session:
        rows = list(await session.scalars(select(AuditEvent).where(AuditEvent.action == ACTION)))
        for row in rows:
            row.details = {
                **row.details,
                "status": "PROCESSING",
                "next_attempt_at": int(now().timestamp()) + 120,
            }
        rows[0].details = {**rows[0].details, "next_attempt_at": 0}
        await session.commit()
    bot = NoticeBot()
    assert await process_scam_announcements(bot, settings, database) == 1
    assert len(bot.calls) == 1


async def test_concurrent_workers_do_not_repeat_notices(journey, database, settings):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900)
    bot = NoticeBot()
    await asyncio.gather(
        process_scam_announcements(bot, settings, database),
        process_scam_announcements(bot, settings, database),
    )
    assert len(bot.calls) == 3


async def test_slow_telegram_keeps_bounded_cycle_and_retries_later(
    journey, database, settings, monkeypatch
):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900)
    monkeypatch.setattr(scam_broadcasts, "DELIVERY_BUDGET", 0.05)

    class SlowBot(NoticeBot):
        async def send_message(self, chat_id, text, **kwargs):
            await asyncio.sleep(1)
            return await super().send_message(chat_id, text, **kwargs)

    assert await process_scam_announcements(SlowBot(), settings, database) == 1
    states = [n.details["status"] for n in await notices(database)]
    assert states.count("RETRY") == 1 and states.count("PENDING") == 2
    monkeypatch.setattr(scam_broadcasts, "DELIVERY_BUDGET", 10.0)
    bot = NoticeBot()
    assert await process_scam_announcements(bot, settings, database) == 2
    assert len(bot.calls) == 2


async def test_worker_sends_honest_summary_and_refresh_does_not_rebroadcast(
    journey, database, settings
):
    await prepare(database, settings)
    await journey.send("/add_sc @example", actor=900)
    bot = NoticeBot()
    assert await process_group_jobs(bot, settings, database) == 3
    assert all("𝗨𝗡𝗞𝗡𝗢𝗪𝗡" in text for _, text, _ in bot.calls)
    async with database() as session:
        core = Service(settings, session)
        record = (await session.scalars(select(ScamRecord))).one()
        await registered_scam_text(None, core, database, record)
    assert await process_scam_announcements(bot, settings, database) == 0


async def test_postgres_bigint_recipients_and_concurrent_claims(postgres_contract):
    database, settings = postgres_contract
    settings = settings.model_copy(update={"group_owner_id": 900, "admin_ids": "900"})
    await prepare(database, settings)
    async with database() as session:
        record_id = (await Service(settings, session).add_scam(900, "@example")).id
    await asyncio.gather(
        queue_scam_announcements(settings, database, record_id),
        queue_scam_announcements(settings, database, record_id),
    )
    assert len(await notices(database)) == 3
    bot = NoticeBot()
    await asyncio.gather(
        process_scam_announcements(bot, settings, database),
        process_scam_announcements(bot, settings, database),
    )
    assert len(bot.calls) == 3
    assert {call[0] for call in bot.calls} == set(GROUPS)

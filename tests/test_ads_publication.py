"""Real SQL state machines, worker fencing and native Telegram request contracts."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message
from sqlalchemy import func, select
from test_integration_contracts import postgres_contract as postgres_contract

import app.ads_publication as module
from app.ads_content import parse_buttons
from app.ads_publication import LEASE_SECONDS, PublicationService
from app.ads_services import AdsService
from app.bot.ads_publisher import AdsPublisher
from app.errors import DomainError
from app.models import (
    AdCampaign,
    AdContentVersion,
    Administrator,
    AdPublicationAttempt,
    AdPublicationPart,
    AdSchedule,
    AuditEvent,
    ManagedGroup,
)

INSTANT = datetime(2026, 11, 1, 10, tzinfo=UTC)
METHOD = SendMessage(chat_id=-100, text="Ad")


@pytest.fixture
def clock(monkeypatch):
    value = [INSTANT]
    monkeypatch.setattr(module, "now", lambda: value[0])
    return value


class Bot:
    id = 777

    def __init__(self):
        self.calls = []
        self.errors = []
        self.roles = {}
        self.on_send = None
        self.read_error = None
        self.sequence = 99

    async def get_chat_member(self, chat, identity):
        if self.read_error:
            raise self.read_error
        return SimpleNamespace(
            user=SimpleNamespace(id=identity), status=self.roles.get(identity, "administrator")
        )

    async def deliver(self, kind, **args):
        self.calls.append((kind, args))
        if self.on_send:
            await self.on_send(kind, args)
        if self.errors:
            error = self.errors.pop(0)
            if error:
                raise error

        def message():
            self.sequence += 1
            return Message(
                message_id=self.sequence,
                date=INSTANT,
                chat=Chat(
                    id=args["chat_id"], type="supergroup" if args["chat_id"] < 0 else "private"
                ),
            )

        return [message() for _ in args["media"]] if kind == "album" else message()

    async def send_message(self, chat_id=None, text=None, **args):
        return await self.deliver("text", chat_id=chat_id, text=text, **args)

    async def send_photo(self, **args):
        return await self.deliver("photo", **args)

    async def send_video(self, **args):
        return await self.deliver("video", **args)

    async def send_media_group(self, **args):
        return await self.deliver("album", **args)


async def configured(sessions, settings, clock, *, album=False, seconds=1800, prefix=""):
    bot = Bot()
    # State-machine tests use a fixed clock; dedicated GLOBAL ADS tests cover
    # positive SQL rate budgets without real-time sleeping or Telegram requests.
    settings.ads_global_messages_per_second = 0
    settings.ads_group_messages_per_minute = 0
    async with sessions() as session:
        ads = AdsService(settings, session)
        campaign = await ads.create(900, "INTERNAL <secret>", prefix + "create-ad")
        draft = await ads.stage(
            900,
            campaign.id,
            1,
            "TEXT",
            {
                "text": "Old <literal> 🍒",
                "entities": [
                    {"type": "custom_emoji", "offset": 14, "length": 2, "custom_emoji_id": "12345"}
                ],
            },
            prefix + "text",
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
        if album:
            draft = await ads.stage(
                900,
                campaign.id,
                2,
                "MEDIA",
                [{"kind": "photo", "file_id": "one"}, {"kind": "video", "file_id": "two"}],
                prefix + "media",
            )
            await ads.mark_previewed(900, draft.id)
            await ads.save(900, draft.id)
            draft = await ads.stage(
                900,
                campaign.id,
                3,
                "BUTTONS",
                [
                    [
                        {
                            "label": "Popup",
                            "kind": "popup",
                            "value": "Original popup",
                            "icon_custom_emoji_id": "54321",
                        }
                    ]
                ],
                prefix + "buttons",
            )
            await ads.mark_previewed(900, draft.id)
            await ads.save(900, draft.id)
        if await session.get(ManagedGroup, -100) is None:
            session.add(ManagedGroup(chat_id=-100, title="Group <&>", approved=True, enabled=True))
        await session.commit()
        pub = PublicationService(settings, session)
        schedule = await pub.ensure(900, campaign.id)
        for field, value in [
            ("chat_id", -100),
            ("interval_seconds", seconds),
            ("start_date", date(2026, 11, 1)),
            ("start_time", "12:00"),
        ]:
            schedule = await pub.change(
                900, campaign.id, schedule.version, field, value, "set-" + field, bot
            )
        base, revision = schedule.version, campaign.version
        schedule = await pub.activate(900, campaign.id, base, revision, bot)
        return campaign.id, schedule.id, bot, base, revision


async def snapshot(sessions, settings):
    async with sessions() as session:
        attempts = list(
            await session.scalars(select(AdPublicationAttempt).order_by(AdPublicationAttempt.id))
        )
        parts = list(
            await session.scalars(select(AdPublicationPart).order_by(AdPublicationPart.id))
        )
        schedules = list(await session.scalars(select(AdSchedule)))
        return attempts, parts, schedules


@pytest.mark.parametrize("seconds", module.INTERVALS)
async def test_real_schedule_activation_and_duplicate_confirmation(
    database, settings, clock, seconds
):
    cid, sid, bot, base, revision = await configured(database, settings, clock, seconds=seconds)
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.activate(900, cid, base, revision, bot)
        assert row.id == sid and row.status == "ACTIVE"
        assert await session.scalar(select(func.count()).select_from(AdSchedule)) == 1
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert len(attempts) == 1 and attempts[0].status == "SENT"
    assert parts[0].message_ids == [100]
    assert schedules[0].next_run_at.replace(tzinfo=UTC) == INSTANT + timedelta(seconds=seconds)
    args = bot.calls[0][1]
    assert args["parse_mode"] is None and args["entities"][0].custom_emoji_id == "12345"
    assert "INTERNAL" not in args["text"]


async def test_pause_resume_stop_and_missed_runs_do_not_burst(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.one(900, cid)
        row = await pub.control(900, cid, row.version, "PAUSED", bot)
        await pub.enqueue()
        assert not (await snapshot(database, settings))[0]
        clock[0] += timedelta(hours=12, minutes=1)
        row = await pub.activate(900, cid, row.version, 2, bot)
        assert row.next_run_at.replace(tzinfo=UTC) == INSTANT + timedelta(hours=12, minutes=30)
    await AdsPublisher(bot, settings, database).tick()
    assert not bot.calls
    clock[0] += timedelta(hours=6)
    await AdsPublisher(bot, settings, database).tick()
    assert not bot.calls  # Old due slot skipped, not backfilled.
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.one(900, cid)
        await pub.control(900, cid, row.version, "STOPPED", bot)
    clock[0] += timedelta(days=1)
    await AdsPublisher(bot, settings, database).tick()
    assert not bot.calls


async def test_restart_reclaims_unsent_claim_but_fences_old_worker(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        old = await pub.claim()
    clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
    async with database() as session:
        current = await PublicationService(settings, session).claim()
        assert current[0] == old[0] and current[2] == old[2] + 1
        assert await PublicationService(settings, session).begin(*old) is None
    await AdsPublisher(bot, settings, database).execute(current)
    assert len(bot.calls) == 1


async def test_restart_during_sending_becomes_uncertain_and_is_never_replayed(
    database, settings, clock
):
    _, _, bot, _, _ = await configured(database, settings, clock, album=True)
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        old = await pub.claim()
        await pub.begin(*old)
    clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "UNCERTAIN"
    assert [p.status for p in parts] == ["UNCERTAIN", "CANCELLED"]
    assert schedules[0].status == "PAUSED"
    assert not any(args["chat_id"] < 0 for _, args in bot.calls)
    async with database() as session:
        assert not await PublicationService(settings, session).result(
            *old, parts[0].id, "SENT", [500]
        )


async def test_album_floodwait_retries_only_unsent_buttons(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock, album=True)
    bot.errors = [None, TelegramRetryAfter(method=METHOD, message="Retry", retry_after=30)]
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == "DEFERRED"
    assert parts[0].status == "SENT" and parts[0].message_ids == [100, 101]
    assert parts[1].status == "DEFERRED"
    await worker.tick()
    assert len(bot.calls) == 2
    clock[0] += timedelta(seconds=31)
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == "SENT"
    assert [kind for kind, _ in bot.calls] == ["album", "text", "text"]
    assert parts[1].message_ids == [102]
    markup = bot.calls[-1][1]["reply_markup"]
    assert markup.inline_keyboard[0][0].icon_custom_emoji_id == "54321"
    assert markup.inline_keyboard[0][0].callback_data.startswith("apb:")


@pytest.mark.parametrize(
    "error",
    [
        TelegramNetworkError(method=METHOD, message="Lost reply"),
        TimeoutError("lost"),
        RuntimeError("bad response"),
    ],
)
async def test_unknown_album_button_result_preserves_partial_success(
    database, settings, clock, error
):
    _, _, bot, _, _ = await configured(database, settings, clock, album=True)
    bot.errors = [None, error]
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "UNCERTAIN" and schedules[0].status == "PAUSED"
    assert [p.status for p in parts] == ["SENT", "UNCERTAIN"]
    assert parts[0].message_ids == [100, 101]
    assert len([c for c in bot.calls if c[1]["chat_id"] < 0]) == 2


@pytest.mark.parametrize(
    "error",
    [
        TelegramForbiddenError(method=METHOD, message="Missing rights"),
        TelegramBadRequest(method=METHOD, message="Bad media"),
    ],
)
async def test_final_telegram_errors_pause_without_infinite_retries(
    database, settings, clock, error
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    bot.errors = [error]
    worker = AdsPublisher(bot, settings, database)
    for _ in range(3):
        await worker.tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "FAILED" and schedules[0].status == "PAUSED"
    assert len([c for c in bot.calls if c[1]["chat_id"] < 0]) == 1
    assert parts[0].message_ids == []


async def test_four_floodwaits_exhaust_retries(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock)
    bot.errors = [
        TelegramRetryAfter(method=METHOD, message="Wait", retry_after=5) for _ in range(4)
    ]
    worker = AdsPublisher(bot, settings, database)
    for _ in range(6):
        await worker.tick()
        clock[0] += timedelta(seconds=6)
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "FAILED" and parts[0].tries == 4
    assert schedules[0].status == "PAUSED"


async def test_content_edit_keeps_schedule_and_pinned_attempt_version(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        claim = await pub.claim()
        schedule = await pub.one(900, cid)
        planned = schedule.next_run_at
        ads = AdsService(settings, session)
        draft = await ads.stage(
            900, cid, 2, "TEXT", {"text": "New content", "entities": []}, "new-content"
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
        assert (await pub.one(900, cid)).next_run_at == planned
    worker = AdsPublisher(bot, settings, database)
    await worker.execute(claim)
    assert bot.calls[0][1]["text"].startswith("Old")
    clock[0] += timedelta(minutes=30)
    await worker.tick()
    assert bot.calls[-1][1]["text"] == "New content"
    attempts, _, _ = await snapshot(database, settings)
    assert attempts[0].content_version_id != attempts[1].content_version_id


@pytest.mark.parametrize("lost", ["global", "actor", "bot", "group", "expiry", "archived"])
async def test_authority_and_expiration_are_checked_after_claim(database, settings, clock, lost):
    if lost == "global":
        settings.admin_ids = "900,999"
        settings.group_owner_id = 999
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        claim = await pub.claim()
        if lost == "global":
            session.add(Administrator(telegram_id=900, active=False, assigned_by=999))
        elif lost == "group":
            (await session.get(ManagedGroup, -100)).enabled = False
        elif lost == "expiry":
            (await session.get(AdSchedule, sid)).end_at = clock[0] + timedelta(seconds=1)
            clock[0] += timedelta(seconds=2)
        elif lost == "archived":
            (await session.get(AdCampaign, cid)).status = "ARCHIVED"
        else:
            bot.roles[900 if lost == "actor" else bot.id] = "member"
        await session.commit()
    await AdsPublisher(bot, settings, database).execute(claim)
    assert not any(args["chat_id"] < 0 for _, args in bot.calls)
    attempts, _, _ = await snapshot(database, settings)
    assert attempts[0].status in {"CANCELLED", "FAILED"}


async def test_end_date_prevents_sends_and_preserves_history(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        (await session.get(AdSchedule, sid)).end_at = clock[0] + timedelta(seconds=1)
        await session.commit()
    clock[0] += timedelta(seconds=1)
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, schedules = await snapshot(database, settings)
    assert not attempts and schedules[0].status == "EXPIRED" and not bot.calls
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(AdContentVersion)) == 2


async def test_invalid_dates_and_stale_confirmation_cannot_activate(database, settings, clock):
    cid, sid, bot, base, revision = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.one(900, cid)
        with pytest.raises(DomainError, match="dates"):
            await pub.change(
                900, cid, row.version, "end_at", INSTANT - timedelta(days=1), "bad-end", bot
            )
        row = await pub.one(900, cid)
        await pub.control(900, cid, row.version, "STOPPED", bot)
        with pytest.raises(DomainError, match="stale"):
            await pub.activate(900, cid, base, revision, bot)


async def race(sessions, settings, clock):
    _, _, bot, _, _ = await configured(sessions, settings, clock)

    async def enqueue():
        async with sessions() as session:
            return await PublicationService(settings, session).enqueue()

    assert sum(await asyncio.gather(*(enqueue() for _ in range(4)))) == 1

    async def claim():
        async with sessions() as session:
            return await PublicationService(settings, session).claim()

    claims = [c for c in await asyncio.gather(*(claim() for _ in range(4))) if c]
    assert len(claims) == 1
    await asyncio.gather(
        *(AdsPublisher(bot, settings, sessions).execute(claims[0]) for _ in range(2))
    )
    assert len(bot.calls) == 1
    assert (await snapshot(sessions, settings))[0][0].status == "SENT"


async def test_sqlite_workers_cannot_double_send(database, settings, clock):
    await race(database, settings, clock)


async def test_postgres_workers_cannot_double_send(postgres_contract, clock):
    sessions, settings = postgres_contract
    await race(sessions, settings, clock)


async def test_pause_between_album_and_buttons_preserves_sent_part(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock, album=True)

    async def pause(kind, args):
        if kind == "album":
            async with database() as session:
                pub = PublicationService(settings, session)
                row = await pub.one(900, cid)
                await pub.control(900, cid, row.version, "PAUSED", bot)

    bot.on_send = pause
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == "CANCELLED"
    assert [p.status for p in parts] == ["SENT", "CANCELLED"]
    assert parts[0].message_ids == [100, 101] and len(bot.calls) == 1


@pytest.mark.parametrize("kind", ["photo", "video"])
async def test_single_media_preserves_caption_entities_and_all_button_types(
    database, settings, clock, kind
):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        ads = AdsService(settings, session)
        draft = await ads.stage(
            900, cid, 2, "MEDIA", [{"kind": kind, "file_id": "file_native"}], "single-media"
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
        buttons = parse_buttons(
            "URL - https://t.me/Example && Popup - popup: Hello\nAlert - alert: Warning\nShare - share: Share this\nCopy - copy: Copy this"
        )
        draft = await ads.stage(900, cid, 3, "BUTTONS", buttons, "all-buttons")
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
    await AdsPublisher(bot, settings, database).tick()
    operation, args = bot.calls[0]
    assert operation == kind and args["parse_mode"] is None
    assert (
        args["caption"] == "Old <literal> 🍒"
        and args["caption_entities"][0].custom_emoji_id == "12345"
    )
    markup = args["reply_markup"].inline_keyboard
    assert markup[0][0].url == "https://t.me/Example"
    assert markup[0][1].callback_data.startswith("apb:")
    assert markup[1][0].callback_data.startswith("apb:")
    assert "share/url?" in markup[2][0].url
    assert markup[3][0].copy_text.text == "Copy this"


async def test_preflight_floodwait_is_bounded_and_never_marks_a_write_started(
    database, settings, clock
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    bot.read_error = TelegramRetryAfter(method=METHOD, message="Read limited", retry_after=5)
    worker = AdsPublisher(bot, settings, database)
    for _ in range(5):
        await worker.tick()
        clock[0] += timedelta(seconds=6)
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "FAILED" and parts[0].tries == 4
    assert schedules[0].status == "PAUSED" and not parts[0].message_ids
    assert not any(args["chat_id"] < 0 for _, args in bot.calls)
    assert len(bot.calls) == 1  # One private notification despite subsequent ticks.


async def test_actual_timeout_leaves_uncertain_and_never_retries(
    database, settings, clock, monkeypatch
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    monkeypatch.setattr("app.bot.ads_publisher.API_TIMEOUT", 0.01)

    async def stuck(kind, args):
        if args["chat_id"] < 0:
            await asyncio.Event().wait()

    bot.on_send = stuck
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    assert (await snapshot(database, settings))[0][0].status == "UNCERTAIN"
    assert len([call for call in bot.calls if call[1]["chat_id"] < 0]) == 1


async def test_empty_new_revision_pauses_and_notifies_once_without_resetting_schedule(
    database, settings, clock
):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        ads = AdsService(settings, session)
        draft = await ads.stage(900, cid, 2, "TEXT", {"text": "", "entities": []}, "empty-edit")
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
    await AdsPublisher(bot, settings, database).tick()
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, schedules = await snapshot(database, settings)
    assert not attempts and schedules[0].status == "PAUSED"
    assert schedules[0].last_error == "ap_content"
    assert len(bot.calls) == 1 and bot.calls[0][1]["chat_id"] == 900


async def test_disabled_group_is_notified_once_after_restart(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        (await session.get(ManagedGroup, -100)).enabled = False
        await session.commit()
    for _ in range(3):
        await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 1 and bot.calls[0][1]["chat_id"] == 900
    assert "AUTHORITY_LOST" in bot.calls[0][1]["text"]


async def test_global_admin_cannot_bypass_actual_group_rights(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    bot.roles[900] = "member"
    async with database() as session:
        pub = PublicationService(settings, session)
        original = (await pub.one(900, cid)).version
        with pytest.raises(DomainError, match="rights"):
            await pub.change(900, cid, original, "interval_seconds", 3600, "no-rights", bot)
        with pytest.raises(DomainError, match="rights"):
            await pub.control(900, cid, original, "STOPPED", bot)
        assert (await pub.one(900, cid)).version == original


async def test_uncertain_statistics_are_not_claimed_as_failed_or_sent(database, settings, clock):
    cid, _, bot, _, _ = await configured(database, settings, clock, album=True)
    bot.errors = [None, TimeoutError()]
    await AdsPublisher(bot, settings, database).tick()
    async with database() as session:
        stats = await PublicationService(settings, session).statistics(900, cid)
        assert stats["sent"] == 0 and stats["failed"] == 0 and stats["uncertain"] == 1
        assert stats["last"] is None


async def test_sending_is_committed_before_telegram_and_no_sql_lock_spans_io(
    database, settings, clock
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    seen = []

    async def inspect_state(kind, args):
        async with database() as session:
            pub = PublicationService(settings, session)
            async with pub.ads.write_system():  # This would block if the sender retained its lock.
                attempt = await session.scalar(select(AdPublicationAttempt))
                part = (await pub.parts(attempt.id))[0]
                seen.append((attempt.status, part.status, attempt.content_version_id))

    bot.on_send = inspect_state
    await asyncio.wait_for(AdsPublisher(bot, settings, database).tick(), 5)
    assert len(seen) == 1 and seen[0][:2] == ("SENDING", "SENDING")


async def test_non_message_api_response_never_becomes_sent(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock)
    original = bot.send_message

    async def invalid(chat_id=None, text=None, **args):
        if chat_id < 0:
            return True
        return await original(chat_id, text, **args)

    bot.send_message = invalid
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert attempts[0].status == "UNCERTAIN" and parts[0].message_ids == []


async def test_floodwait_is_shared_and_cannot_be_bypassed_by_stopping_campaign(
    database, settings, clock
):
    cid, _, bot, _, _ = await configured(database, settings, clock)
    await configured(database, settings, clock, prefix="second-")
    bot.errors = [TelegramRetryAfter(method=METHOD, message="Wait", retry_after=30)]
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.one(900, cid)
        await pub.control(900, cid, row.version, "STOPPED", bot)
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 1
    clock[0] += timedelta(seconds=31)
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 2
    attempts, _, _ = await snapshot(database, settings)
    assert [row.status for row in attempts] == ["CANCELLED", "SENT"]


async def test_existing_removed_group_marker_overrides_stale_enabled_flags(
    database, settings, clock
):
    _, _, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        session.add(AuditEvent(actor_id=900, action="group_removed", details={"chat_id": -100}))
        await session.commit()
    await AdsPublisher(bot, settings, database).tick()
    assert not any(args["chat_id"] < 0 for _, args in bot.calls)
    assert (await snapshot(database, settings))[2][0].status == "PAUSED"


@pytest.mark.parametrize("state", ["PENDING", "CLAIMED", "DEFERRED", "PARTIAL"])
async def test_restart_discards_obsolete_waiting_attempt_then_uses_future_slot(
    database, settings, clock, state
):
    _, _, bot, _, _ = await configured(database, settings, clock, album=state == "PARTIAL")
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        if state != "PENDING":
            claim = await pub.claim()
            if state in {"DEFERRED", "PARTIAL"}:
                row, part, _ = await pub.begin(*claim)
                if state == "PARTIAL":
                    assert await pub.result(*claim, part.id, "SENT", [501, 502])
                    row, part, _ = await pub.begin(*claim)
                await pub.result(*claim, part.id, "DEFERRED", error="429", retry=30)
    clock[0] += timedelta(hours=6)
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, schedules = await snapshot(database, settings)
    assert [a.status for a in attempts] == ["CANCELLED"]
    assert not bot.calls
    assert schedules[0].next_run_at.replace(tzinfo=UTC) == clock[0] + timedelta(minutes=30)
    clock[0] += timedelta(minutes=30)
    await AdsPublisher(bot, settings, database).tick()
    attempts, parts, _ = await snapshot(database, settings)
    assert [a.status for a in attempts] == ["CANCELLED", "SENT"]
    assert attempts[0].error == "OBSOLETE_SLOT"
    assert attempts[1].due_at.replace(tzinfo=UTC) == clock[0]
    assert len([c for c in bot.calls if c[1]["chat_id"] < 0]) == (2 if state == "PARTIAL" else 1)
    if state == "PARTIAL":
        assert parts[0].status == "SENT" and parts[0].message_ids == [501, 502]


@pytest.mark.parametrize("outcome", ["FAILED", "UNCERTAIN", "LEASE"])
@pytest.mark.parametrize("policy", ["STOPPED", "EXPIRED"])
async def test_old_inflight_failure_cannot_overwrite_stopped_or_expired_policy(
    database, settings, clock, outcome, policy
):
    cid, sid, bot, _, _ = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        await pub.enqueue()
        claim = await pub.claim()
        _, part, _ = await pub.begin(*claim)
        row = await pub.one(900, cid)
        if policy == "STOPPED":
            await pub.control(900, cid, row.version, policy, bot)
        else:
            row.end_at = clock[0] + timedelta(seconds=1)
            await session.commit()
            clock[0] += timedelta(seconds=1)
            await pub.enqueue()
        if outcome == "LEASE":
            clock[0] += timedelta(seconds=LEASE_SECONDS + 1)
            assert await pub.claim() is None
        else:
            await pub.result(*claim, part.id, outcome, error="Rejected or lost reply")
    attempts, _, schedules = await snapshot(database, settings)
    assert attempts[0].status == ("UNCERTAIN" if outcome == "LEASE" else outcome)
    assert schedules[0].status == policy
    assert schedules[0].next_run_at is None


async def test_partial_album_response_retains_confirmed_ids_without_claiming_success(
    database, settings, clock
):
    _, _, bot, _, _ = await configured(database, settings, clock, album=True)

    async def partial(**args):
        bot.calls.append(("album", args))
        return [Message(message_id=501, date=INSTANT, chat=Chat(id=-100, type="supergroup"))]

    bot.send_media_group = partial
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "UNCERTAIN" and schedules[0].status == "PAUSED"
    assert [p.status for p in parts] == ["UNCERTAIN", "CANCELLED"]
    assert parts[0].message_ids == [501]
    assert len([c for c in bot.calls if c[1]["chat_id"] < 0]) == 1


async def test_expiry_during_album_preserves_media_and_cancels_buttons(database, settings, clock):
    _, sid, bot, _, _ = await configured(database, settings, clock, album=True)
    async with database() as session:
        (await session.get(AdSchedule, sid)).end_at = clock[0] + timedelta(seconds=1)
        await session.commit()

    async def expire(kind, args):
        if kind == "album":
            clock[0] += timedelta(seconds=1)

    bot.on_send = expire
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    attempts, parts, schedules = await snapshot(database, settings)
    assert attempts[0].status == "CANCELLED" and schedules[0].status == "EXPIRED"
    assert [p.status for p in parts] == ["SENT", "CANCELLED"]
    assert parts[0].message_ids == [100, 101] and len(bot.calls) == 1


async def test_content_save_during_api_write_keeps_pinned_revision(database, settings, clock):
    cid, sid, bot, _, _ = await configured(database, settings, clock)

    async def edit(kind, args):
        async with database() as session:
            row = await session.get(AdSchedule, sid)
            before = row.version, row.next_run_at, row.interval_seconds
            ads = AdsService(settings, session)
            draft = await ads.stage(
                900, cid, 2, "TEXT", {"text": "Changed during send", "entities": []}, "during-send"
            )
            await ads.mark_previewed(900, draft.id)
            await ads.save(900, draft.id)
            await session.refresh(row)
            assert (row.version, row.next_run_at, row.interval_seconds) == before

    bot.on_send = edit
    await AdsPublisher(bot, settings, database).tick()
    assert bot.calls[0][1]["text"].startswith("Old")
    bot.on_send = None
    clock[0] += timedelta(minutes=30)
    await AdsPublisher(bot, settings, database).tick()
    assert bot.calls[-1][1]["text"] == "Changed during send"


@pytest.mark.parametrize("revoked", ["local", "global"])
async def test_authority_revoked_before_activation_confirmation(database, settings, clock, revoked):
    settings.admin_ids, settings.group_owner_id = "900,999", 999
    cid, sid, bot, _, revision = await configured(database, settings, clock)
    async with database() as session:
        pub = PublicationService(settings, session)
        row = await pub.one(900, cid)
        row = await pub.control(900, cid, row.version, "PAUSED", bot)
        confirmation = row.version
        if revoked == "global":
            session.add(Administrator(telegram_id=900, active=False, assigned_by=999))
            await session.commit()
        else:
            bot.roles[900] = "member"
        with pytest.raises(DomainError):
            await pub.activate(900, cid, confirmation, revision, bot)
        await session.rollback()
        row = await session.get(AdSchedule, sid, populate_existing=True)
        assert row.status == "PAUSED" and row.version == confirmation


async def test_simultaneous_floodwaits_keep_longest_global_deadline(database, settings, clock):
    _, _, bot, _, _ = await configured(database, settings, clock)
    await configured(database, settings, clock, prefix="other-")
    async with database() as session:
        pub = PublicationService(settings, session)
        assert await pub.enqueue() == 2
        claims = [await pub.claim(), await pub.claim()]
    both_sending = asyncio.Event()
    entered = 0

    async def synchronize(kind, args):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_sending.set()
        await asyncio.wait_for(both_sending.wait(), 3)

    bot.on_send = synchronize
    bot.errors = [
        TelegramRetryAfter(method=METHOD, message="Wait", retry_after=delay) for delay in [60, 30]
    ]
    await asyncio.gather(*(AdsPublisher(bot, settings, database).execute(c) for c in claims))
    bot.on_send = None
    clock[0] += timedelta(seconds=31)
    await AdsPublisher(bot, settings, database).tick()
    assert len(bot.calls) == 2
    clock[0] += timedelta(seconds=30)
    await AdsPublisher(bot, settings, database).tick()
    attempts, _, _ = await snapshot(database, settings)
    assert [a.status for a in attempts] == ["SENT", "SENT"] and len(bot.calls) == 4


async def test_worker_loop_starts_cancels_and_restarts_without_duplicate(
    database, settings, clock, monkeypatch
):
    from app.bot.ads_publisher import ads_worker

    _, _, bot, _, _ = await configured(database, settings, clock)
    completed = asyncio.Event()
    original = AdsPublisher.tick

    async def tick(self):
        await original(self)
        attempts, _, _ = await snapshot(database, settings)
        if attempts and attempts[0].status == "SENT":
            completed.set()

    monkeypatch.setattr(AdsPublisher, "tick", tick)
    for _ in range(2):
        completed.clear()
        task = asyncio.create_task(ads_worker(bot, settings, database))
        try:
            await asyncio.wait_for(completed.wait(), 5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    assert len(bot.calls) == 1
    assert (await snapshot(database, settings))[0][0].status == "SENT"


async def test_scheduled_publication_preserves_rich_emoji_entities_without_extra_slot(
    database, settings, clock
):
    from test_ads_content import rich_emoji_text

    cid, sid, bot, _, _ = await configured(database, settings, clock)
    text, entities = rich_emoji_text()
    entities.append({"type": "bold", "offset": 0, "length": 200})
    async with database() as session:
        ads = AdsService(settings, session)
        before = await session.get(AdSchedule, sid)
        planned = before.next_run_at
        draft = await ads.stage(
            900,
            cid,
            (await ads.get(900, cid)).version,
            "TEXT",
            {"text": text, "entities": entities},
            "rich-custom-emoji",
        )
        await ads.mark_previewed(900, draft.id)
        await ads.save(900, draft.id)
        assert (await session.get(AdSchedule, sid)).next_run_at == planned
    worker = AdsPublisher(bot, settings, database)
    await worker.tick()
    await worker.tick()
    group_calls = [args for _, args in bot.calls if args["chat_id"] < 0]
    assert len(group_calls) == 1
    assert group_calls[0]["text"] == text and group_calls[0]["parse_mode"] is None
    assert [
        e.model_dump(mode="json", exclude_none=True) for e in group_calls[0]["entities"]
    ] == entities
    attempts, _, _ = await snapshot(database, settings)
    assert len(attempts) == 1 and attempts[0].status == "SENT"

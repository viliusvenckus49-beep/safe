"""Strict numeric identity and honest, durable SCAM ban outcomes without real Telegram calls."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import BanChatMember
from sqlalchemy import func, select
from structlog.testing import capture_logs

from app.bot.group_runtime import process_group_jobs, process_scam_bans
from app.group_services import GroupService
from app.models import BanAction, ManagedGroup, ScamRecord, now
from app.scam_management import ScamManagement
from app.services import Service

pytestmark = pytest.mark.asyncio


class BanBot:
    def __init__(self, failures=None, *, false=False):
        self.calls = []
        self.failures = failures or {}
        self.false = false

    async def ban_chat_member(self, *, chat_id, user_id):
        self.calls.append((chat_id, user_id))
        if chat_id in self.failures:
            kind = self.failures[chat_id]
            kwargs = {"retry_after": 600} if kind is TelegramRetryAfter else {}
            raise kind(
                method=BanChatMember(chat_id=chat_id, user_id=user_id),
                message="Not enough rights to restrict this member",
                **kwargs,
            )
        return not self.false


async def prepare(database, settings, *, groups=5, target="22", permission=True):
    async with database() as session:
        core = Service(settings, session)
        protected = GroupService(settings, session)
        for index in range(groups):
            await protected.register_group(900, -1000 - index, f"Group {index}", permission)
        record = await core.add_scam(900, target, "Confirmed fraudulent activity")
        return record.id


@pytest.mark.parametrize("failures", [0, 2, 5])
async def test_real_group_outcomes_and_failures_remain_pending(database, settings, failures):
    record_id = await prepare(database, settings)
    bot = BanBot({-1000 - index: TelegramBadRequest for index in range(failures)})
    with capture_logs() as logs:
        summary = await process_scam_bans(bot, settings, database, record_id)
    assert (summary.checked, summary.succeeded, summary.failed, summary.pending) == (
        5,
        5 - failures,
        failures,
        failures,
    )
    assert summary.telegram_id == 22
    assert len(bot.calls) == 5

    assert all(user_id == 22 for _, user_id in bot.calls)
    attempts = [event for event in logs if event["event"] == "group_ban_result"]
    assert len(attempts) == 5
    assert all(event["operation"] == "ban_chat_member" for event in attempts)
    assert sum(event["success"] for event in attempts) == 5 - failures
    assert all(
        event["user_id"] == 22 and event["scam_record_id"] == record_id for event in attempts
    )
    errors = [event for event in attempts if not event["success"]]
    assert all(event["error_type"] == "TelegramBadRequest" for event in errors)
    assert all("rights" in event["reason"] for event in errors)
    assert await process_group_jobs(bot, settings, database) == 0
    assert (await process_scam_bans(bot, settings, database, record_id)).succeeded == 5 - failures
    assert len(bot.calls) == 5


async def test_already_banned_is_verified_by_telegram_and_not_rebanned(database, settings):
    record_id = await prepare(database, settings)

    class ExistingBanBot(BanBot):
        async def get_chat_member(self, *, chat_id, user_id):
            return SimpleNamespace(status="kicked" if chat_id in {-1000, -1001} else "left")

    bot = ExistingBanBot(
        {-1002: TelegramBadRequest, -1003: TelegramBadRequest, -1004: TelegramBadRequest}
    )
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert (summary.checked, summary.succeeded, summary.already_banned, summary.pending) == (
        5,
        2,
        2,
        3,
    )
    assert {chat_id for chat_id, _ in bot.calls} == {-1002, -1003, -1004}


async def test_member_lookup_failure_still_attempts_preemptive_ban(database, settings):
    record_id = await prepare(database, settings, groups=1)

    class UnknownBot(BanBot):
        async def get_chat_member(self, *, chat_id, user_id):
            raise TelegramBadRequest(
                method=BanChatMember(chat_id=chat_id, user_id=user_id),
                message="USER_NOT_PARTICIPANT",
            )

    bot = UnknownBot()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert summary.succeeded == 1 and summary.already_banned == 0
    assert bot.calls == [(-1000, 22)]


@pytest.mark.parametrize("reason", ["PARTICIPANT_ID_INVALID", "USER_NOT_PARTICIPANT"])
async def test_identity_refusal_retries_after_another_group_resolves_user(
    database, settings, reason
):
    record_id = await prepare(database, settings, groups=3)
    async with database() as session:
        first = await session.scalar(select(BanAction).order_by(BanAction.id).limit(1))
        first_chat = first.chat_id

    class ResolvingBot(BanBot):
        resolved = False

        async def ban_chat_member(self, *, chat_id, user_id):
            self.calls.append((chat_id, user_id))
            if chat_id == first_chat and not self.resolved:
                raise TelegramBadRequest(
                    method=BanChatMember(chat_id=chat_id, user_id=user_id),
                    message=reason,
                )
            self.resolved = True
            return True

    bot = ResolvingBot()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert (summary.succeeded, summary.pending) == (2, 1)
    async with database() as session:
        failed = await session.scalar(select(BanAction).where(BanAction.chat_id == first_chat))
        assert failed.status == "FAILED" and failed.attempts == 1
        assert failed.next_attempt_at.replace(tzinfo=now().tzinfo) > now()
        failed.next_attempt_at = now() - timedelta(seconds=1)
        await session.commit()
    # No member message or join event is needed to retry the preemptive ban.
    assert await process_group_jobs(bot, settings, database) == 1
    async with database() as session:
        groups = GroupService(settings, session)
        summary = await groups.ban_summary(record_id)
        retried = await session.scalar(select(BanAction).where(BanAction.chat_id == first_chat))
        assert summary.succeeded == summary.checked == 3 and summary.pending == 0
        assert retried.status == "SUCCEEDED" and retried.attempts == 2


async def test_unknown_id_never_issues_ban_and_admin_link_starts_all_groups(database, settings):
    record_id = await prepare(database, settings, target="@unknown_user")
    bot = BanBot()
    unknown = await process_scam_bans(bot, settings, database, record_id)
    assert unknown.telegram_id is None and unknown.checked == unknown.pending == 0
    assert not bot.calls
    async with database() as session:
        core = Service(settings, session)
        await ScamManagement(core).supplement(900, record_id, "id", "7681768804", "a" * 32)
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 5
    known = await process_scam_bans(bot, settings, database, record_id)
    assert known.telegram_id == 7681768804 and known.succeeded == known.checked == 5
    assert all(user_id == 7681768804 for _, user_id in bot.calls)


async def test_unknown_scam_not_automatically_claimed_by_same_username(database, settings):
    record_id = await prepare(database, settings, target="@unknown_user")
    async with database() as session:
        await Service(settings, session).observe(44, "unknown_user", "A different current owner")
    bot = BanBot()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert summary.telegram_id is None
    assert not bot.calls


async def test_trusted_numeric_observation_initiates_queue_for_existing_scam(database, settings):
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.register_group(900, -1001, "One", True)
        await groups.register_group(900, -1002, "Two", True)
        core = Service(settings, session)
        user = await core.resolve("22")
        record = ScamRecord(target_id=user.id, moderator_id=900, reason="Existing confirmed record")
        session.add(record)
        await session.commit()
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 0
        observed = await core.observe(22, "updated_username", "Updated name")
        assert observed.telegram_id == 22
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 2
        record_id = record.id
    bot = BanBot()
    assert (await process_scam_bans(bot, settings, database, record_id)).succeeded == 2


async def test_username_change_keeps_numeric_scam_and_ban_history(database, settings):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot()
    assert (await process_scam_bans(bot, settings, database, record_id)).succeeded == 1
    async with database() as session:
        core = Service(settings, session)
        before = await core.resolve("22")
        internal_id = before.id
        after = await core.observe(22, "brand_new_username", "Still same person")
        assert after.id == internal_id
        assert (await core.repo.active_scam(after.id)).id == record_id
        assert (await session.scalar(select(BanAction))).status == "SUCCEEDED"
    assert (await process_scam_bans(bot, settings, database, record_id)).succeeded == 1
    assert bot.calls == [(-1000, 22)]


async def test_missing_permissions_are_attempted_and_recorded_pending(database, settings):
    record_id = await prepare(database, settings, groups=1, permission=False)
    bot = BanBot({-1000: TelegramForbiddenError})
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert (summary.checked, summary.succeeded, summary.failed, summary.pending) == (1, 0, 1, 1)
    async with database() as session:
        group = await session.get(ManagedGroup, -1000)
        assert group.approved and group.enabled and not group.can_restrict_members
        assert (await session.scalar(select(BanAction))).result_type == "TelegramForbiddenError"
        await GroupService(settings, session).note_group_permissions(-1000, True)
    bot.failures.clear()
    assert (await process_scam_bans(bot, settings, database, record_id)).succeeded == 1
    assert len(bot.calls) == 2


@pytest.mark.parametrize("fresh_join", [True, False])
async def test_join_and_message_rearm_failed_bans(database, settings, fresh_join):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot({-1000: TelegramBadRequest})
    assert (await process_scam_bans(bot, settings, database, record_id)).pending == 1
    async with database() as session:
        # The first real appearance retries immediately after a preemptive failure.
        await GroupService(settings, session).check_member(-1000, 22, fresh_join=fresh_join)
    bot.failures.clear()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert summary.succeeded == 1 and summary.pending == 0
    assert len(bot.calls) == 2


async def test_repeat_failed_messages_are_throttled_after_first_presence(database, settings):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot({-1000: TelegramBadRequest})
    await process_scam_bans(bot, settings, database, record_id)
    async with database() as session:
        await GroupService(settings, session).check_member(-1000, 22)
    await process_scam_bans(bot, settings, database, record_id)
    assert len(bot.calls) == 2
    for _ in range(3):
        async with database() as session:
            await GroupService(settings, session).check_member(-1000, 22)
        await process_scam_bans(bot, settings, database, record_id)
    assert len(bot.calls) == 2


async def test_permission_refresh_never_bypasses_telegram_retry_after(database, settings):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot({-1000: TelegramRetryAfter})
    await process_scam_bans(bot, settings, database, record_id)
    async with database() as session:
        groups = GroupService(settings, session)
        await groups.note_group_permissions(-1000, True)
        await groups.register_group(900, -1000, "Still protected", True)
    assert (await process_scam_bans(bot, settings, database, record_id)).pending == 1
    assert len(bot.calls) == 1


async def test_timed_out_attempt_leaves_overflow_unclaimed(database, settings, monkeypatch):
    from app.bot import group_runtime

    record_id = await prepare(database, settings)
    clock = iter([0.0, 0.0, 21.0])
    monkeypatch.setattr(group_runtime, "monotonic", lambda: next(clock))

    class TimeoutBot(BanBot):
        async def ban_chat_member(self, *, chat_id, user_id):
            self.calls.append((chat_id, user_id))
            raise TimeoutError

    bot = TimeoutBot()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert (summary.checked, summary.succeeded, summary.failed, summary.pending) == (5, 0, 1, 5)
    assert len(bot.calls) == 1
    async with database() as session:
        jobs = list((await session.scalars(select(BanAction))).all())
        assert sum(job.status == "PENDING" for job in jobs) == 4
        assert not any(job.status == "PROCESSING" for job in jobs)
        assert sum(job.attempts for job in jobs) == 1


async def test_group_messages_do_not_remove_retry_after_or_hammer_api(database, settings):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot({-1000: TelegramRetryAfter})
    await process_scam_bans(bot, settings, database, record_id)
    async with database() as session:
        job = await session.scalar(select(BanAction))
        job.completed_at = now() - timedelta(minutes=2)
        await session.commit()
        groups = GroupService(settings, session)
        await groups.check_member(-1000, 22, fresh_join=True)
        await groups.check_member(-1000, 22)
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert summary.pending == 1 and summary.succeeded == 0
    assert len(bot.calls) == 1


async def test_api_false_is_never_successful(database, settings):
    record_id = await prepare(database, settings, groups=1)
    summary = await process_scam_bans(BanBot(false=True), settings, database, record_id)
    assert (summary.succeeded, summary.failed, summary.pending) == (0, 1, 1)
    async with database() as session:
        job = await session.scalar(select(BanAction))
        assert job.status == "FAILED" and job.result_type == "API_FALSE"


async def test_unexpected_truthy_api_response_is_not_a_success(database, settings):
    record_id = await prepare(database, settings, groups=1)

    class InvalidBot(BanBot):
        async def ban_chat_member(self, *, chat_id, user_id):
            return {"error": "unexpected transport response"}

    summary = await process_scam_bans(InvalidBot(), settings, database, record_id)
    assert (summary.succeeded, summary.failed, summary.pending) == (0, 1, 1)


async def test_concurrent_interactive_claims_never_duplicate_ban(database, settings):
    record_id = await prepare(database, settings, groups=1)
    bot = BanBot()
    summaries = await asyncio.gather(
        process_scam_bans(bot, settings, database, record_id),
        process_scam_bans(bot, settings, database, record_id),
    )
    assert len(bot.calls) == 1
    assert any(summary.succeeded == 1 for summary in summaries)
    async with database() as session:
        assert (await session.scalar(select(BanAction))).attempts == 1


async def test_changed_record_identity_invalidates_old_numeric_job(database, settings):
    record_id = await prepare(database, settings, groups=1)
    async with database() as session:
        new_target = await Service(settings, session).resolve("44")
        record = await session.get(ScamRecord, record_id)
        record.target_id = new_target.id
        await session.commit()
    bot = BanBot()
    summary = await process_scam_bans(bot, settings, database, record_id)
    assert summary.telegram_id == 44 and summary.succeeded == 1
    assert bot.calls == [(-1000, 44)]
    async with database() as session:
        old = await session.scalar(select(BanAction).where(BanAction.telegram_id == 22))
        assert old.status == "OBSOLETE"


async def test_database_identity_lock_not_held_during_telegram_request(database, settings):
    record_id = await prepare(database, settings, groups=1)

    class InspectBot(BanBot):
        async def ban_chat_member(self, *, chat_id, user_id):
            async def other_write():
                async with database() as other:
                    await Service(settings, other).observe(33, "another_member", "Another")

            await asyncio.wait_for(other_write(), timeout=2)
            return await super().ban_chat_member(chat_id=chat_id, user_id=user_id)

    assert (await process_scam_bans(InspectBot(), settings, database, record_id)).succeeded == 1

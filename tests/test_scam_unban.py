"""Unban buttons preserve history and rely on actual Telegram outcomes."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import UnbanChatMember
from sqlalchemy import select
from test_integration_contracts import postgres_contract as postgres_fixture
from test_mtproto_relay import prepare as prepare_relay
from test_telegram import journey as telegram_journey

from app.bot.callbacks import ScamAdmin
from app.bot.group_runtime import _execute_bans
from app.bot.scam_admin import controls, refresh_controls
from app.bot.scam_unban import attempt_unban, process_scam_unban
from app.errors import DomainError
from app.group_services import GroupService
from app.i18n import t, use_language
from app.models import AuditEvent, BanAction, ManagedGroup, now
from app.mtproto_relay import set_relay
from app.services import Service

journey = telegram_journey
postgres_contract = postgres_fixture


async def prepare(database, settings, target="42"):
    async with database() as session:
        groups = GroupService(settings, session)
        for chat in (-1000, -1001):
            await groups.register_group(900, chat, f"Group {chat}", True)
        return (await Service(settings, session).add_scam(900, target)).id


def bot():
    return SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="kicked")),
        unban_chat_member=AsyncMock(return_value=True),
    )


async def test_unban_archives_scam_preserves_data_and_audits_each_group(database, settings):
    async with database() as session:
        await Service(settings, session).set_trusted(900, "42", True, "unban-trusted")
    record_id = await prepare(database, settings)
    api = bot()
    async with database() as session:
        core = Service(settings, session)
        await core.admin_adjust_rep(900, "42", 3, "Reviewed feedback", "unban-rep")
        record, summary = await process_scam_unban(api, core, record_id, 900)
        assert record.status == "REMOVED" and record.removed_by == 900
        assert (await core.repo.rep_stats(record.target_id))[0] == 3
        assert (await core.repo.trusted_designation(record.target_id)).active
        assert [r["result"] for r in summary["groups"]] == ["UNBANNED", "UNBANNED"]
        jobs = list((await session.scalars(select(BanAction))).all())
        assert len(jobs) == 2 and all(job.status == "OBSOLETE" for job in jobs)
        attempts = list(
            (
                await session.scalars(
                    select(AuditEvent).where(AuditEvent.action == "scam_unban_attempt")
                )
            ).all()
        )
        assert len(attempts) == 2 and all(e.details["success"] for e in attempts)
        await GroupService(settings, session).check_member(-1000, 42, fresh_join=True)
        assert not await GroupService(settings, session).claim_bans(scam_record_id=record_id)
    assert api.unban_chat_member.await_count == 2
    assert all(c.kwargs["only_if_banned"] is True for c in api.unban_chat_member.await_args_list)


async def test_partial_failure_is_reported_and_can_be_retried(database, settings, monkeypatch):
    record_id = await prepare(database, settings)
    api = bot()
    api.unban_chat_member.side_effect = [
        TelegramBadRequest(
            method=UnbanChatMember(chat_id=-1000, user_id=42), message="not enough rights"
        ),
        True,
    ]
    async with database() as session:
        core = Service(settings, session)
        _, summary = await process_scam_unban(api, core, record_id, 900)
        assert sum(r["success"] for r in summary["groups"]) == 1
        failed = next(r for r in summary["groups"] if not r["success"])
        assert failed["result"] == "TelegramBadRequest" and failed["reason"] == "not enough rights"
        with pytest.raises(DomainError, match="cooldown"):
            await process_scam_unban(api, core, record_id, 900)
    future = now() + timedelta(seconds=11)
    monkeypatch.setattr("app.bot.scam_unban.now", lambda: future)
    api.unban_chat_member.side_effect = None
    async with database() as session:
        record, summary = await process_scam_unban(api, Service(settings, session), record_id, 900)
        assert record.status == "REMOVED" and all(r["success"] for r in summary["groups"])
        removals = list(
            (
                await session.scalars(select(AuditEvent).where(AuditEvent.action == "scam_removed"))
            ).all()
        )
        assert len(removals) == 1


@pytest.mark.parametrize("status", ["member", "administrator", "creator", "restricted", "left"])
async def test_already_unbanned_members_are_never_removed(status):
    api = bot()
    api.get_chat_member.return_value.status = status
    assert await attempt_unban(api, -1000, 42) == (True, "ALREADY_UNBANNED")
    api.unban_chat_member.assert_not_awaited()


async def test_api_false_and_unknown_peer_are_not_claimed_as_success():
    api = bot()
    api.unban_chat_member.return_value = False
    assert await attempt_unban(api, -1000, 42) == (False, "API_FALSE")
    api.get_chat_member.side_effect = TelegramBadRequest(
        method=UnbanChatMember(chat_id=-1000, user_id=42), message="USER_NOT_PARTICIPANT"
    )
    assert await attempt_unban(api, -1000, 42) == (False, "API_FALSE")


async def test_retry_after_stops_other_calls_and_is_persisted(database, settings, monkeypatch):
    record_id = await prepare(database, settings)
    api = bot()
    api.unban_chat_member.side_effect = TelegramRetryAfter(
        method=UnbanChatMember(chat_id=-1000, user_id=42), message="Flood", retry_after=60
    )
    async with database() as session:
        _, summary = await process_scam_unban(api, Service(settings, session), record_id, 900)
        assert all(not r["success"] and r["retry_after"] == 60 for r in summary["groups"])
    assert api.unban_chat_member.await_count == 1
    future = now() + timedelta(seconds=11)
    monkeypatch.setattr("app.bot.scam_unban.now", lambda: future)
    async with database() as session:
        with pytest.raises(DomainError, match="cooldown"):
            await process_scam_unban(api, Service(settings, session), record_id, 900)


async def test_unknown_id_and_later_scam_activation_reject_old_button(database, settings):
    record_id = await prepare(database, settings, "@unknownscam")
    api = bot()
    async with database() as session:
        core = Service(settings, session)
        with pytest.raises(DomainError):
            await process_scam_unban(api, core, record_id, 900)
        assert (await core.repo.scam_by_id(record_id)).status == "ACTIVE"
        known = await core.add_scam(900, "42")
        await core.remove_scam(900, "42", "Administrator removal", record_id=known.id)
        current = await core.add_scam(900, "42")
        with pytest.raises(DomainError):
            await process_scam_unban(api, core, known.id, 900)
        assert (await core.repo.scam_by_id(current.id)).status == "ACTIVE"
    api.unban_chat_member.assert_not_awaited()


async def test_disabled_and_unapproved_groups_are_excluded(database, settings):
    record_id = await prepare(database, settings)
    api = bot()
    async with database() as session:
        (await session.get(ManagedGroup, -1000)).enabled = False
        (await session.get(ManagedGroup, -1001)).approved = False
        await session.commit()
        _, summary = await process_scam_unban(api, Service(settings, session), record_id, 900)
        assert summary["groups"] == []
    api.unban_chat_member.assert_not_awaited()


@pytest.mark.parametrize("action", ["unban", "unban_receipt"])
async def test_unban_callbacks_require_private_admin(
    journey, database, settings, monkeypatch, action
):
    record_id = await prepare(database, settings)
    api = bot()
    monkeypatch.setattr(Bot, "get_chat_member", api.get_chat_member)
    monkeypatch.setattr(Bot, "unban_chat_member", api.unban_chat_member)
    callback = ScamAdmin(action=action, value=str(record_id)).pack()
    await journey.click(callback, actor=1)
    await journey.click(callback, actor=900, chat=-123)
    api.unban_chat_member.assert_not_awaited()
    await journey.click(callback, actor=900)
    async with database() as session:
        assert (await Service(settings, session).repo.scam_by_id(record_id)).status == "REMOVED"
    assert api.unban_chat_member.await_count == 2


@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_unban_is_visible_in_details_and_private_controls(
    journey, database, settings, monkeypatch, lang
):
    record_id = await prepare(database, settings)
    api = bot()
    monkeypatch.setattr(Bot, "get_chat_member", api.get_chat_member)
    monkeypatch.setattr(Bot, "unban_chat_member", api.unban_chat_member)
    async with database() as session:
        core = Service(settings, session)
        await core.set_language(900, lang)
        record = await core.repo.scam_by_id(record_id)
        with use_language(lang):
            for markup in (
                controls(record),
                controls(record, persistent=True),
                refresh_controls(record, allow_remove=True),
            ):
                assert any(b.text == t("sm.unban") for row in markup.inline_keyboard for b in row)
            assert not any(
                b.text == t("sm.unban")
                for row in refresh_controls(record).inline_keyboard
                for b in row
            )
    await journey.click(ScamAdmin(action="details", value=str(record_id)).pack(), actor=900)
    await journey.click(ScamAdmin(action="unban", value=str(record_id)).pack(), actor=900)
    assert await journey.state(900) is None
    assert (await journey.data(900))["screen_message_id"] is None
    assert "2/2" in journey.transport.calls[-1].text
    assert t("sm.unban_retry", lang) in [
        b.text for row in journey.transport.calls[-1].reply_markup.inline_keyboard for b in row
    ]


async def check_inflight_order(database, settings):
    record_id = await prepare(database, settings)
    entered, release = asyncio.Event(), asyncio.Event()
    sequence = []
    api = bot()

    async def ban(**kwargs):
        sequence.append("ban-start")
        entered.set()
        await release.wait()
        sequence.append("ban-finish")
        return True

    async def unban(**kwargs):
        sequence.append("unban")
        return True

    api.ban_chat_member = ban
    api.unban_chat_member.side_effect = unban
    api.get_chat_member.return_value.status = "unknown"
    async with database() as session:
        groups = GroupService(settings, session)
        jobs = await groups.claim_bans(limit=1, scam_record_id=record_id)
        ban_task = asyncio.create_task(_execute_bans(api, groups, jobs))
        await asyncio.wait_for(entered.wait(), 2)
        async with database() as unban_session:
            unban_task = asyncio.create_task(
                process_scam_unban(api, Service(settings, unban_session), record_id, 900)
            )
            await asyncio.sleep(0)
            assert not unban_task.done()
            release.set()
            await asyncio.wait_for(asyncio.gather(ban_task, unban_task), 20)
        assert sequence[:2] == ["ban-start", "ban-finish"] and "unban" in sequence[2:]
        assert not await groups.claim_bans(scam_record_id=record_id)


async def test_inflight_ban_finishes_before_unban(database, settings):
    await check_inflight_order(database, settings)


async def test_postgres_inflight_ban_finishes_before_unban(postgres_contract):
    database, settings = postgres_contract
    settings.group_owner_id = 900
    await check_inflight_order(database, settings)


async def test_group_help_unban_is_authorized_and_only_after_removal(database):
    _, record_id, relay, client = await prepare_relay(database)
    assert not await relay.dispatch_unban(900, record_id)
    assert not client.calls
    async with database() as session:
        await Service(relay.settings, session).remove_scam(
            900, "22", "UNBAN review", record_id=record_id
        )
    with pytest.raises(DomainError):
        await relay.dispatch_unban(1, record_id)
    assert await relay.dispatch_unban(900, record_id)
    assert [request.message for request in client.calls] == ["/unban 22"]
    async with database() as session:
        await Service(relay.settings, session).add_scam(900, "22")
    assert not await relay.dispatch_unban(900, record_id)
    assert len(client.calls) == 1


async def test_staff_submission_is_not_counted_as_unban_success(database):
    settings, record_id, relay, _ = await prepare_relay(database)
    set_relay(relay)
    api = bot()
    api.unban_chat_member.return_value = False
    try:
        async with database() as session:
            _, summary = await process_scam_unban(api, Service(settings, session), record_id, 900)
            assert summary["staff"] == "submitted"
            assert all(not r["success"] for r in summary["groups"])
    finally:
        set_relay(None)


async def test_withdrawn_staff_scope_never_sends_global_unban(database):
    settings, record_id, relay, client = await prepare_relay(database)
    async with database() as session:
        await Service(settings, session).remove_scam(900, "22", "UNBAN review", record_id=record_id)
        (await session.get(ManagedGroup, -1000)).enabled = False
        await session.commit()
    assert not await relay.dispatch_unban(900, record_id)
    assert client.calls == []


async def test_scam_removal_receipt_still_offers_unban(journey, database, settings, monkeypatch):
    record_id = await prepare(database, settings)
    api = bot()
    monkeypatch.setattr(Bot, "get_chat_member", api.get_chat_member)
    monkeypatch.setattr(Bot, "unban_chat_member", api.unban_chat_member)
    await journey.click(ScamAdmin(action="remove", value=str(record_id)).pack(), actor=900)
    unban = ScamAdmin(action="unban_receipt", value=str(record_id)).pack()
    assert unban in [
        b.callback_data
        for row in journey.transport.calls[-1].reply_markup.inline_keyboard
        for b in row
    ]
    api.unban_chat_member.assert_not_awaited()
    await journey.click(unban, actor=900)
    assert api.unban_chat_member.await_count == 2

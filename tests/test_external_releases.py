"""Untracked Telegram restrictions require scoped approval; API is a stateful double."""

from datetime import timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import SendMessage, UnbanChatMember
from sqlalchemy import func, select
from test_group_moderation_foundation import CHAT, OTHER, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_private_panel_lifecycle import ephemeral_journey as ephemeral_journey
from test_private_panel_lifecycle import private_click
from test_warning_controls import click

from app.bot.callbacks import LocalAction
from app.bot.local_moderation import MUTE_PERMISSIONS, LocalModerationExecutor
from app.bot.moderation_messages import HEADINGS
from app.errors import DomainError
from app.i18n import t
from app.models import (
    AuditEvent,
    GroupModerationDraft,
    GroupSanction,
    GroupSanctionJob,
    ScamRecord,
    User,
    now,
)
from app.moderation_services import GroupModerationService
from app.services import Service


def restricted(native, kind, *, partial=False, until=None):
    if kind == "UNBAN":
        member = native.peer(51, "kicked", until_date=until)
    else:
        values = MUTE_PERMISSIONS.model_dump(exclude_none=True)
        if partial:
            values["can_send_polls"] = True
        member = native.peer(51, "restricted", is_member=True, until_date=until, **values)
    native.members[CHAT, 51] = member
    return member


def public(journey):
    return [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage) and c.chat_id == CHAT and not c.ephemeral_message_parameters
    ]


@pytest.mark.parametrize("kind", ["UNBAN", "UNMUTE"])
@pytest.mark.parametrize("lang", ["lt", "en", "ru"])
async def test_untracked_release_private_confirm_once(
    ephemeral_journey, database, settings, kind, lang
):
    journey, native, panels = ephemeral_journey
    restricted(native, kind, partial=True)
    async with database() as session:
        await Service(settings, session).set_language(42, lang)
    await journey.send("/" + kind.lower() + " 51", actor=42, chat=CHAT)
    assert native.calls == [] and not public(journey)
    prompt = next(iter(panels.values()))
    assert t("lm.control.external_title", lang) in prompt.text
    assert kind in prompt.text
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0
    button = prompt.reply_markup.inline_keyboard[0][0].callback_data
    await private_click(journey, prompt, button)
    assert len(native.calls) == 1
    assert native.calls[0][0] == ("unban" if kind == "UNBAN" else "restrict")
    assert not panels
    notices = public(journey)
    assert len(notices) == 1 and notices[0].text.startswith(HEADINGS[kind])
    async with database() as session:
        row = await session.scalar(select(GroupSanction))
        assert row.owned is False and row.status == "REVOKED"
        assert await session.scalar(select(func.count()).select_from(GroupSanctionJob)) == 1
        event = await session.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "group_moderation_external_release_confirmed"
            )
        )
        assert event.actor_id == 42 and event.details["chat_id"] == CHAT
    await private_click(journey, prompt, button)
    await LocalModerationExecutor(database, journey.bot).process()
    assert len(native.calls) == 1 and len(public(journey)) == 1


@pytest.mark.parametrize("kind", ["UNBAN", "UNMUTE"])
async def test_other_actor_cannot_confirm_private_release(ephemeral_journey, database, kind):
    journey, native, panels = ephemeral_journey
    restricted(native, kind)
    native.admin(CHAT, 43)
    await journey.send("/" + kind.lower() + " 51", actor=42, chat=CHAT)
    prompt = next(iter(panels.values()))
    await private_click(
        journey, prompt, prompt.reply_markup.inline_keyboard[0][0].callback_data, actor=43
    )
    assert not native.calls and panels and not public(journey)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0


@pytest.mark.parametrize(
    "change", ["rights", "bot_rights", "deadline", "permissions", "scam", "expired", "admin_target"]
)
async def test_release_revalidates_at_confirmation(ephemeral_journey, database, change):
    journey, native, panels = ephemeral_journey
    member = restricted(native, "UNMUTE")
    await journey.send("/unmute 51", actor=42, chat=CHAT)
    prompt = next(iter(panels.values()))
    if change == "rights":
        native.members[CHAT, 42].can_restrict_members = False
    elif change == "bot_rights":
        native.members[CHAT, native.id].can_restrict_members = False
    elif change == "deadline":
        member.until_date = now() + timedelta(days=1)
    elif change == "permissions":
        member.can_send_polls = True
    elif change == "admin_target":
        native.admin(CHAT, 51)
    else:
        async with database() as session:
            if change == "expired":
                draft = await session.scalar(select(GroupModerationDraft))
                draft.expires_at = now() - timedelta(seconds=1)
            else:
                user = User(telegram_id=51)
                session.add(user)
                await session.flush()
                session.add(ScamRecord(target_id=user.id, moderator_id=900, reason="Protected"))
            await session.commit()
    await private_click(journey, prompt, prompt.reply_markup.inline_keyboard[0][0].callback_data)
    assert not native.calls and not public(journey)
    async with database() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanctionJob)) == 0


async def test_cancelled_external_release_never_executes(ephemeral_journey, database):
    journey, native, panels = ephemeral_journey
    restricted(native, "UNBAN")
    await journey.send("/unban 51", actor=42, chat=CHAT)
    prompt = next(iter(panels.values()))
    await private_click(journey, prompt, prompt.reply_markup.inline_keyboard[0][1].callback_data)
    assert not panels and not native.calls
    async with database() as session:
        assert (await session.scalar(select(GroupModerationDraft))).status == "CANCELLED"


@pytest.mark.parametrize("outcome", ["timeout", "rejected", "flood"])
async def test_external_release_preserves_unknown_and_retry_states(
    ephemeral_journey, database, outcome
):
    journey, native, panels = ephemeral_journey
    restricted(native, "UNBAN")
    await journey.send("/unban 51", actor=42, chat=CHAT)
    prompt = next(iter(panels.values()))
    method = UnbanChatMember(chat_id=CHAT, user_id=51, only_if_banned=True)
    native.error = (
        TimeoutError()
        if outcome == "timeout"
        else TelegramRetryAfter(method=method, message="Flood", retry_after=300)
        if outcome == "flood"
        else TelegramBadRequest(method=method, message="not enough rights")
    )

    async def fail_unban(**kwargs):
        native.calls.append(("unban", kwargs))
        raise native.error

    native.unban_chat_member = fail_unban
    await private_click(journey, prompt, prompt.reply_markup.inline_keyboard[0][0].callback_data)
    assert not public(journey)
    async with database() as session:
        job = await session.scalar(select(GroupSanctionJob))
        assert (
            job.status
            == {"timeout": "UNCERTAIN", "rejected": "FAILED", "flood": "PENDING"}[outcome]
        )
    before = len(native.calls)
    await LocalModerationExecutor(database, journey.bot).process()
    assert len(native.calls) == before


async def test_worker_cannot_release_without_durable_explicit_confirmation(database):
    native = NativeBot()
    await enabled(database, native)
    restricted(native, "UNBAN")
    async with database() as session:
        row = GroupSanction(
            chat_id=CHAT,
            telegram_id=51,
            kind="BAN",
            actor_id=42,
            reason="",
            status="REVOKING",
            owned=False,
            policy_version=0,
            request_key="unowned-old-ban",
        )
        session.add(row)
        await session.flush()
        session.add(GroupSanctionJob(sanction_id=row.id, operation="RELEASE", actor_id=42))
        await session.commit()
    await LocalModerationExecutor(database, native).process()
    assert not native.calls


async def test_postgres_competing_external_approvals_create_one_release(postgres_contract):
    import asyncio

    factory, _ = postgres_contract
    native = NativeBot()
    await enabled(factory, native)
    native.admin(CHAT, 43)
    restricted(native, "UNBAN")
    ids = []
    for actor in (42, 43):
        async with factory() as session:
            draft = await GroupModerationService(session, native).prepare(
                CHAT,
                actor,
                51,
                "UNBAN",
                until_date=None,
                reason="",
                request="external-" + str(actor),
            )
            ids.append((draft.id, actor))

    async def confirm_one(did, actor):
        async with factory() as session:
            try:
                return (await GroupModerationService(session, native).confirm(did, actor)).id
            except DomainError:
                return None

    results = await asyncio.gather(*(confirm_one(*value) for value in ids))
    assert sum(value is not None for value in results) == 1
    await LocalModerationExecutor(factory, native).process()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(GroupSanctionJob)) == 1
    assert len(native.calls) == 1
    assert native.members.get((OTHER, 51)) is None


@pytest.mark.parametrize("kind", ["BAN", "MUTE"])
async def test_manual_clear_allows_a_fresh_sanction(username_journey, database, kind):
    journey, native = username_journey
    await journey.send("/" + kind.lower() + " 51 1d", actor=42, chat=CHAT)
    assert len(native.calls) == 1
    native.members[CHAT, 51] = native.peer(51)  # Telegram's manual UNBAN / UNMUTE.
    await journey.send("/" + kind.lower() + " 51 1d", actor=42, chat=CHAT)
    assert len(native.calls) == 2
    async with database() as session:
        rows = list(await session.scalars(select(GroupSanction).order_by(GroupSanction.id)))
        assert [row.status for row in rows] == ["COMPLETED", "ACTIVE"]
        assert rows[0].owned and rows[1].owned
        release = await session.scalar(
            select(GroupSanctionJob).where(
                GroupSanctionJob.sanction_id == rows[0].id,
                GroupSanctionJob.operation == "RELEASE",
            )
        )
        assert release.status == "CANCELLED" and release.result == "TELEGRAM_STATE_CHANGED"


@pytest.mark.parametrize("kind", ["BAN", "KICK"])
async def test_stronger_command_works_over_a_manual_mute(username_journey, kind):
    journey, native = username_journey
    restricted(native, "UNMUTE", partial=True)
    await journey.send("/" + kind.lower() + " 51", actor=42, chat=CHAT)
    assert [call[0] for call in native.calls] == (["ban"] if kind == "BAN" else ["ban", "unban"])


@pytest.mark.parametrize("change", ["state", "actor", "scam"])
async def test_worker_rechecks_external_approval_before_api(database, change):
    native = NativeBot()
    await enabled(database, native)
    member = restricted(native, "UNBAN")
    async with database() as session:
        local = GroupModerationService(session, native)
        draft = await local.prepare(
            CHAT, 42, 51, "UNBAN", until_date=None, reason="", request="worker-external-release"
        )
        row = await local.confirm(draft.id, 42)
        sid = row.id
    if change == "state":
        member.until_date = now() + timedelta(days=1)
    elif change == "actor":
        native.members[CHAT, 42].can_restrict_members = False
    else:
        async with database() as session:
            user = User(telegram_id=51)
            session.add(user)
            await session.flush()
            session.add(ScamRecord(target_id=user.id, moderator_id=900, reason="Protected"))
            await session.commit()
    await LocalModerationExecutor(database, native).process(sanction_id=sid)
    assert not native.calls


async def test_old_owned_button_requires_new_approval_after_manual_change(
    ephemeral_journey, database
):
    journey, native, panels = ephemeral_journey
    await journey.send("/mute 51 1d", actor=42, chat=CHAT)
    original = public(journey)[-1]
    async with database() as session:
        mid = (await session.scalar(select(GroupModerationDraft))).panel_message_id
    native.members[CHAT, 51].until_date += timedelta(days=1)
    native.members[CHAT, 51].can_send_polls = True
    await click(
        journey,
        original.reply_markup.inline_keyboard[0][0].callback_data,
        actor=42,
        message_id=mid,
    )
    assert len(native.calls) == 1 and len(panels) == 1
    prompt = next(iter(panels.values()))
    await private_click(journey, prompt, prompt.reply_markup.inline_keyboard[0][0].callback_data)
    assert len(native.calls) == 2 and not panels


async def test_leaving_private_external_preview_retires_its_confirmation(
    username_journey, database
):
    journey, native = username_journey
    restricted(native, "UNBAN")
    await journey.click(LocalAction(action="action", chat=CHAT, value="UNBAN").pack(), actor=42)
    await journey.send("51", actor=42)
    async with database() as session:
        draft = await session.scalar(select(GroupModerationDraft))
        did, mid = draft.id, draft.panel_message_id
    await journey.click(LocalAction(action="group", chat=CHAT).pack(), actor=42, message_id=mid)
    await journey.click(LocalAction(action="confirm", value=did).pack(), actor=42, message_id=mid)
    assert not native.calls
    async with database() as session:
        assert (await session.get(GroupModerationDraft, did)).status == "CANCELLED"
        assert await session.scalar(select(func.count()).select_from(GroupSanction)) == 0


async def test_group_confirmation_cannot_be_replayed_as_a_dm_menu_callback(
    ephemeral_journey, database
):
    journey, native, _ = ephemeral_journey
    restricted(native, "UNBAN")
    await journey.send("/unban 51", actor=42, chat=CHAT)
    async with database() as session:
        did = (await session.scalar(select(GroupModerationDraft))).id
    await journey.click(LocalAction(action="confirm", value=did).pack(), actor=42)
    assert not native.calls


@pytest.mark.parametrize(
    "kind,command,notice",
    [("UNBAN", "unmute", "already_banned"), ("UNMUTE", "unban", "already_unbanned")],
)
async def test_reverse_command_reports_only_its_own_kind(username_journey, kind, command, notice):
    journey, native = username_journey
    restricted(native, kind)
    await journey.send("/" + command + " 51", actor=42, chat=CHAT)
    replies = [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage) and c.ephemeral_message_parameters
    ]
    assert replies and t("lm.diagnostic." + notice + ".title") in replies[-1].text
    assert not native.calls


async def test_mute_can_further_restrict_a_partially_restricted_member(username_journey):
    journey, native = username_journey
    member = restricted(native, "UNMUTE", partial=True)
    member.can_send_messages = True
    await journey.send("/mute 51 1h", actor=42, chat=CHAT)
    assert len(native.calls) == 1 and native.calls[0][0] == "restrict"
    assert native.members[CHAT, 51].can_send_messages is False


@pytest.mark.parametrize("status", ["left", "restricted"])
async def test_departed_user_is_not_muted(username_journey, status):
    journey, native = username_journey
    native.members[CHAT, 51] = native.peer(51, status, is_member=False)
    await journey.send("/mute 51 1h", actor=42, chat=CHAT)
    assert not native.calls
    replies = [
        c
        for c in journey.transport.calls
        if isinstance(c, SendMessage) and c.ephemeral_message_parameters
    ]
    assert replies and t("lm.diagnostic.not_in_group.title") in replies[-1].text

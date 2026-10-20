"""Existing MTProto may discover targets; Bot API and local rights stay authoritative."""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, update
from test_group_moderation_foundation import CHAT, NativeBot
from test_integration_contracts import postgres_contract as postgres_contract
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import known, no_sanctions
from test_moderation_usernames import username_journey as username_journey

from app.errors import DomainError
from app.models import GroupModerationDraft, GroupSanction, ManagedGroup, ScamRecord, User
from app.moderation_services import GroupModerationService
from app.telegram_failures import IdentityLookup

pytestmark = pytest.mark.asyncio


class Lookup:
    def __init__(self, user=None, *, error=None, session=None):
        self.user, self.error, self.session = user, error, session
        self.calls = []

    async def lookup_identity(self, name, *, refresh=False):
        self.calls.append((name, refresh))
        if self.session is not None:
            assert not self.session.in_transaction()  # No SQL lock during MTProto network work.
        if self.error:
            raise self.error
        return IdentityLookup(user=self.user, code="resolved" if self.user else "unavailable")


def peer(user_id=51, username="unseen_user", **extra):
    return SimpleNamespace(id=user_id, username=username, **extra)


async def unseen_contract(factory, monkeypatch):
    native = NativeBot()
    await enabled(factory, native)
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "unseen_user"
    async with factory() as session:
        relay = Lookup(peer(), session=session)
        monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
        local = GroupModerationService(session, native, use_mtproto_lookup=True)
        assert await local.resolve_username(CHAT, 42, "@UNSEEN_USER") == 51
        assert relay.calls == [("unseen_user", True)]
        draft = await local.prepare(
            CHAT, 42, 51, "WARN", until_date=None, reason="test", request="mtproto-username-001"
        )
        sanction = await local.confirm(draft.id, 42)
        assert sanction.telegram_id == 51 and sanction.status == "RECORDED"
        assert await session.scalar(select(func.count()).select_from(User)) == 0
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0
    assert native.calls == []  # Lookup never issues a Telegram sanction itself.


async def test_mtproto_discovers_unseen_target_without_global_identity_writes(
    database, monkeypatch
):
    await unseen_contract(database, monkeypatch)


async def test_postgres_mtproto_identity_isolation(postgres_contract, monkeypatch):
    factory, _ = postgres_contract
    await unseen_contract(factory, monkeypatch)


async def test_command_wires_only_explicitly_enabled_existing_relay(
    username_journey, database, settings, monkeypatch
):
    journey, native = username_journey
    monkeypatch.setattr(settings, "group_help_enabled", True)
    relay = Lookup(peer())
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "unseen_user"
    await journey.send("/warn @unseen_user test", actor=42, chat=CHAT)
    assert relay.calls == [("unseen_user", True)]
    async with database() as session:
        row = await session.scalar(select(GroupSanction))
        assert row is not None and row.telegram_id == 51 and row.status == "RECORDED"
        assert await session.scalar(select(User).where(User.telegram_id == 51)) is None
        assert await session.scalar(select(func.count()).select_from(ScamRecord)) == 0


async def test_disconnected_or_disabled_relay_never_resolves_unknown_user(database, monkeypatch):
    native = NativeBot()
    await enabled(database, native)
    relay = Lookup(peer())
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    async with database() as session:
        with pytest.raises(DomainError, match="username_unknown"):
            await GroupModerationService(session, native).resolve_username(CHAT, 42, "@unseen_user")
    assert relay.calls == []
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: None)
    async with database() as session:
        with pytest.raises(DomainError, match="username_unknown"):
            await GroupModerationService(session, native, use_mtproto_lookup=True).resolve_username(
                CHAT, 42, "@unseen_user"
            )
    await no_sanctions(database)


@pytest.mark.parametrize("error", [None, TimeoutError(), OSError()])
async def test_unavailable_relay_keeps_live_verified_cached_fallback(database, monkeypatch, error):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native)
    relay = Lookup(error=error)
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    async with database() as session:
        local = GroupModerationService(session, native, use_mtproto_lookup=True)
        assert await local.resolve_username(CHAT, 42, "@target_user") == 51
        with pytest.raises(DomainError, match="username_unknown"):
            await local.resolve_username(CHAT, 42, "@unseen_user")
    await no_sanctions(database)


@pytest.mark.parametrize(
    "remote",
    [
        peer(True),
        peer(0),
        peer(-51),
        peer("51"),
        peer(username="wrong_user"),
        peer(username=None),
        peer(deleted=True),
    ],
)
async def test_invalid_remote_identity_is_never_trusted(database, monkeypatch, remote):
    native = NativeBot()
    await enabled(database, native)
    relay = Lookup(remote)
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    async with database() as session:
        with pytest.raises(DomainError):
            await GroupModerationService(session, native, use_mtproto_lookup=True).resolve_username(
                CHAT, 42, "@unseen_user"
            )
    await no_sanctions(database)


async def test_mtproto_identity_still_needs_matching_bot_api_member(database, monkeypatch):
    native = NativeBot()
    await enabled(database, native)
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "different_user"
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: Lookup(peer()))
    async with database() as session:
        with pytest.raises(DomainError, match="username_changed"):
            await GroupModerationService(session, native, use_mtproto_lookup=True).resolve_username(
                CHAT, 42, "@unseen_user"
            )
    await no_sanctions(database)


async def test_fresh_relay_resolves_current_owner_without_rewriting_old_cache(
    database, monkeypatch
):
    native = NativeBot()
    await enabled(database, native)
    await known(database, native, username="unseen_user")
    native.members[CHAT, 51].user.username = "old_name"
    native.members[CHAT, 52] = native.peer(52)
    native.members[CHAT, 52].user.username = "unseen_user"
    relay = Lookup(peer(52))
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    async with database() as session:
        assert (
            await GroupModerationService(session, native, use_mtproto_lookup=True).resolve_username(
                CHAT, 42, "@unseen_user"
            )
            == 52
        )
        assert (
            await session.scalar(select(User).where(User.telegram_id == 51))
        ).username == "unseen_user"
        assert await session.scalar(select(User).where(User.telegram_id == 52)) is None
    await no_sanctions(database)


@pytest.mark.parametrize("denial", ["actor", "group"])
async def test_local_authorization_precedes_any_account_lookup(database, monkeypatch, denial):
    native = NativeBot()
    await enabled(database, native)
    relay = Lookup(peer())
    monkeypatch.setattr("app.mtproto_relay.active_relay", lambda: relay)
    async with database() as session:
        if denial == "group":
            await session.execute(
                update(ManagedGroup).where(ManagedGroup.chat_id == CHAT).values(enabled=False)
            )
            await session.commit()
        with pytest.raises(DomainError):
            await GroupModerationService(session, native, use_mtproto_lookup=True).resolve_username(
                CHAT, 900 if denial == "actor" else 42, "@unseen_user"
            )
        assert await session.scalar(select(func.count()).select_from(GroupModerationDraft)) == 0
    assert relay.calls == []

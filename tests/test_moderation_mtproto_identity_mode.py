"""Moderation username discovery must not require or activate staff transport."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, update
from test_group_moderation_foundation import CHAT, NativeBot
from test_local_moderation_stage2 import enabled
from test_local_moderation_stage2 import local_journey as local_journey
from test_moderation_usernames import username_journey as username_journey
from test_mtproto_relay import prepare as prepare_bans

from app.bot.group_runtime import process_group_jobs
from app.config import Settings
from app.models import AuditEvent, BanAction, GroupSanction, ScamRecord, User, now
from app.moderation_services import GroupModerationService
from app.mtproto_relay import GroupHelpRelay, _UsernameOnlyClient


class Client:
    def __init__(self, *, authorized=True, me=True, bot=False, error=None):
        self.authorized, self.me, self.bot, self.error = authorized, me, bot, error
        self.connected, self.closed, self.requests = False, False, []

    async def connect(self):
        if self.error:
            raise self.error
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def get_me(self):
        return SimpleNamespace(id=8896102663, bot=self.bot) if self.me else None

    async def disconnect(self):
        self.closed = True

    async def get_entity(self, *args):
        raise AssertionError("Identity mode must not access a staff group/bot")

    async def get_participants(self, *args):
        raise AssertionError("Identity mode must not inspect staff members")

    async def __call__(self, request):
        from telethon.tl.functions.contacts import ResolveUsernameRequest

        assert isinstance(request, ResolveUsernameRequest)
        self.requests.append(request)
        return SimpleNamespace(
            peer=SimpleNamespace(user_id=51),
            users=[SimpleNamespace(id=51, username="unseen_user", deleted=False)],
        )


def config(**overrides):
    return Settings(
        bot_token="123456:TEST_ONLY",
        admin_ids="42",
        _env_file=None,
        **{"moderation_mtproto_enabled": True, **overrides},
    )


def patch_client(monkeypatch, client):
    monkeypatch.setattr("telethon.TelegramClient", lambda *a, **k: client)
    monkeypatch.setattr(
        "app.mtproto_relay.private_configuration", lambda p: {"api_id": 123, "api_hash": "0" * 32}
    )


def test_lookup_only_config_requires_no_staff_group_bot_or_scope():
    settings = config()
    assert settings.moderation_mtproto_enabled and not settings.group_help_enabled
    assert settings.group_help_bot_id == 0 and not settings.group_help_scope
    assert not Settings(bot_token="123:TEST", _env_file=None).moderation_mtproto_enabled
    with pytest.raises(ValidationError):
        config(group_help_state_dir="relative/session")
    with pytest.raises(ValidationError):
        config(group_help_enabled=True)  # Full staff mode keeps its existing requirements.


@pytest.mark.asyncio
async def test_lookup_without_staff_and_without_global_side_effects(database, monkeypatch):
    client = Client()
    patch_client(monkeypatch, client)
    native = NativeBot()
    await enabled(database, native)
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "unseen_user"
    relay = await GroupHelpRelay.connect_identity(config(), database)
    assert relay.staff is None and client.connected
    monkeypatch.setattr("app.mtproto_relay._relay", relay)
    try:
        async with database() as session:
            assert (
                await GroupModerationService(
                    session, native, use_mtproto_lookup=True
                ).resolve_username(CHAT, 42, "@unseen_user")
                == 51
            )
        async with database() as session:
            for model in (User, ScamRecord, BanAction, AuditEvent):
                # Group enrollment audits precede the lookup; no staff activation audit.
                if model is AuditEvent:
                    assert not await session.scalar(
                        select(AuditEvent.id).where(AuditEvent.action == "group_help_activated")
                    )
                else:
                    assert await session.scalar(select(func.count()).select_from(model)) == 0
        assert len(client.requests) == 1
        assert await process_group_jobs(SimpleNamespace(), config(), database) == 0
    finally:
        await relay.close()
    assert client.closed


@pytest.mark.asyncio
async def test_native_command_uses_explicit_lookup_mode_without_staff(
    username_journey, database, settings, monkeypatch
):
    journey, native = username_journey
    monkeypatch.setattr(settings, "group_help_enabled", False)
    monkeypatch.setattr(settings, "moderation_mtproto_enabled", True)
    client = Client()
    patch_client(monkeypatch, client)
    relay = await GroupHelpRelay.connect_identity(settings, database)
    monkeypatch.setattr("app.mtproto_relay._relay", relay)
    native.members[CHAT, 51] = native.peer(51)
    native.members[CHAT, 51].user.username = "unseen_user"
    try:
        await journey.send("/warn @unseen_user test", actor=42, chat=CHAT)
        async with database() as session:
            row = await session.scalar(select(GroupSanction))
            assert row is not None and row.telegram_id == 51 and row.status == "RECORDED"
            assert await session.scalar(select(User).where(User.telegram_id == 51)) is None
        assert len(client.requests) == 1
    finally:
        await relay.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options", [{"authorized": False}, {"me": False}, {"bot": True}, {"error": OSError()}]
)
async def test_invalid_identity_startup_disconnects(database, monkeypatch, options):
    client = Client(**options)
    patch_client(monkeypatch, client)
    with pytest.raises((ValueError, OSError)):
        await GroupHelpRelay.connect_identity(config(), database)
    assert client.closed and not client.requests


@pytest.mark.asyncio
async def test_disabled_lookup_cannot_connect(database, monkeypatch):
    client = Client()
    patch_client(monkeypatch, client)
    with pytest.raises(ValueError, match="disabled"):
        await GroupHelpRelay.connect_identity(config(moderation_mtproto_enabled=False), database)
    assert not client.connected


@pytest.mark.asyncio
async def test_lookup_mode_restores_durable_floodwait(database, monkeypatch):
    until = now() + timedelta(seconds=120)
    async with database() as session:
        session.add(
            AuditEvent(
                actor_id=42, action="mtproto_flood_wait", details={"until": until.isoformat()}
            )
        )
        await session.commit()
    client = Client()
    patch_client(monkeypatch, client)
    relay = await GroupHelpRelay.connect_identity(config(), database)
    try:
        result = await relay.lookup_identity("unseen_user", refresh=True)
        assert result.code == "rate_limit" and result.retry_after > 0
        assert relay.flood_until == until and not client.requests
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_lookup_startup_does_not_requeue_staff_ban_failures(database, monkeypatch):
    await prepare_bans(database, "22")
    async with database() as session:
        await session.execute(update(BanAction).values(status="FAILED"))
        await session.commit()
    client = Client()
    patch_client(monkeypatch, client)
    relay = await GroupHelpRelay.connect_identity(config(), database)
    try:
        async with database() as session:
            rows = list(await session.scalars(select(BanAction)))
            assert len(rows) == 2 and all(row.status == "FAILED" for row in rows)
    finally:
        await relay.close()


@pytest.mark.asyncio
async def test_identity_transport_refuses_staff_message_requests():
    from telethon.tl.functions.messages import SendMessageRequest
    from telethon.tl.types import InputPeerSelf

    client = Client()
    wrapper = _UsernameOnlyClient(client)
    with pytest.raises(ValueError, match="non-lookup"):
        await wrapper(SendMessageRequest(peer=InputPeerSelf(), message="/ban 51", random_id=1))
    assert not client.requests

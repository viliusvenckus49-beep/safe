from datetime import timedelta
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import select

from app.bot.group_runtime import process_scam_bans
from app.config import Settings
from app.group_services import GroupService
from app.models import AuditEvent, BanAction, ScamRecord, now
from app.mtproto_relay import GroupHelpRelay, private_configuration, set_relay
from app.services import Service


class Client:
    def __init__(self, username="scammer", user_id=22):
        self.calls = []
        self.username, self.user_id = username, user_id
        self.before_resolve = None
        self.error = None

    async def get_input_entity(self, staff):
        from telethon.tl.types import InputPeerChat

        return InputPeerChat(555)

    async def __call__(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        if hasattr(request, "username"):
            if self.before_resolve:
                await self.before_resolve()
            return SimpleNamespace(
                peer=SimpleNamespace(user_id=self.user_id),
                users=[
                    SimpleNamespace(
                        id=self.user_id,
                        username=self.username,
                        deleted=False,
                        first_name="Original name",
                        last_name="Text",
                    )
                ],
            )
        return SimpleNamespace()  # Request accepted does not assert a successful ban.


def relay_settings():
    return Settings(
        bot_token="123456:TEST_TOKEN",
        admin_ids="900",
        _env_file=None,
        group_help_enabled=True,
        group_help_bot_id=99,
        group_help_scope_ids="-1000,-1001",
    )


async def prepare(database, target="22"):
    settings = relay_settings()
    async with database() as session:
        groups = GroupService(settings, session)
        for chat in (-1000, -1001):
            await groups.register_group(900, chat, "Test group", True)
        record = await Service(settings, session).add_scam(900, target)
    client = Client()
    relay = GroupHelpRelay(settings, database, client, SimpleNamespace(id=555))
    return settings, record.id, relay, client


@pytest.fixture(autouse=True)
def clear_relay():
    set_relay(None)
    yield
    set_relay(None)


@pytest.mark.asyncio
async def test_fresh_username_resolution_saves_id_and_existing_ban_outbox(database):
    settings, record_id, relay, client = await prepare(database, "@scammer")
    assert await relay.resolve_record(record_id)
    assert client.calls[0].username == "scammer"
    async with database() as session:
        record = await Service(settings, session).repo.scam_by_id(record_id)
        assert record.target.telegram_id == 22
        assert record.target.display_name == "Original name Text"
        jobs = (await session.scalars(select(BanAction))).all()
        assert len(jobs) == 2 and all(j.telegram_id == 22 for j in jobs)
    assert not await relay.resolve_record(record_id)
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_resolver_does_not_overwrite_known_numeric_identity(database):
    settings, record_id, relay, client = await prepare(database, "23")
    assert not await relay.resolve_record(record_id)
    assert client.calls == []
    async with database() as session:
        assert (
            await Service(settings, session).repo.scam_by_id(record_id)
        ).target.telegram_id == 23


@pytest.mark.asyncio
async def test_wrong_current_username_cannot_create_numeric_bans(database):
    settings, record_id, relay, client = await prepare(database, "@scammer")
    client.username = "different"
    assert not await relay.resolve_record(record_id)
    async with database() as session:
        assert (
            await Service(settings, session).repo.scam_by_id(record_id)
        ).target.telegram_id is None
        assert not (await session.scalars(select(BanAction))).all()
    assert not await relay.resolve_record(record_id)  # Persisted attempt throttle.
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_identity_race_rechecks_username_before_committing(database):
    settings, record_id, relay, client = await prepare(database, "@scammer")

    async def change_owner():
        async with database() as session:
            await Service(settings, session).observe(33, "scammer", "New observed account")

    client.before_resolve = change_owner
    assert not await relay.resolve_record(record_id)
    async with database() as session:
        record = await Service(settings, session).repo.scam_by_id(record_id)
        assert record.target.telegram_id is None
        assert not (await session.scalars(select(BanAction))).all()


@pytest.mark.asyncio
async def test_unknown_id_refresh_resets_bounded_resolver_attempts_without_confirmation(database):
    settings, record_id, relay, client = await prepare(database, "@scammer")
    async with database() as session:
        record = await Service(settings, session).repo.scam_by_id(record_id)
        session.add(
            AuditEvent(
                actor_id=900,
                action="mtproto_identity_attempt",
                target_id=record.target_id,
                details={"record_id": record_id, "attempt": 8},
                created_at=now() - timedelta(minutes=3),
            )
        )
        await session.commit()
    assert not await relay.resolve_record(record_id)
    async with database() as session:
        await GroupService(settings, session).retry_scam_bans(900, record_id)
    assert await relay.resolve_record(record_id)


class Bot:
    def __init__(self, client, *, verified=True, already=()):
        self.client, self.verified, self.already = client, verified, already
        self.bans = []

    async def get_chat_member(self, *, chat_id, user_id):
        sent = any(hasattr(r, "message") for r in self.client.calls)
        return SimpleNamespace(
            status="kicked" if chat_id in self.already or (sent and self.verified) else "left"
        )

    async def ban_chat_member(self, *, chat_id, user_id):
        self.bans.append((chat_id, user_id))
        return False


@pytest.mark.asyncio
async def test_one_plain_numeric_staff_command_and_real_per_group_results(database):
    settings, record_id, relay, client = await prepare(database)
    set_relay(relay)
    bot = Bot(client)
    result = await process_scam_bans(bot, settings, database, record_id)
    assert (result.succeeded, result.pending, result.already_banned) == (2, 0, 1)
    commands = [request for request in client.calls if hasattr(request, "message")]
    assert len(commands) == 1 and commands[0].message == "/ban 22"
    assert commands[0].random_id > 0
    assert bot.bans == []


@pytest.mark.asyncio
async def test_accepted_staff_command_is_not_falsified_as_success(database):
    settings, record_id, relay, client = await prepare(database)
    set_relay(relay)
    result = await process_scam_bans(Bot(client, verified=False), settings, database, record_id)
    assert (result.succeeded, result.pending) == (0, 2)
    assert len([r for r in client.calls if hasattr(r, "message")]) == 1


@pytest.mark.asyncio
async def test_existing_bans_do_not_send_staff_command(database):
    settings, record_id, relay, client = await prepare(database)
    set_relay(relay)
    result = await process_scam_bans(
        Bot(client, already=(-1000, -1001)), settings, database, record_id
    )
    assert result.already_banned == 2 and not client.calls


@pytest.mark.asyncio
async def test_group_withdrawal_prevents_global_staff_command(database):
    settings, record_id, relay, client = await prepare(database)
    async with database() as session:
        await GroupService(settings, session).remove_group(900, -1001)
    set_relay(relay)
    result = await process_scam_bans(Bot(client), settings, database, record_id)
    assert not client.calls
    assert result.checked == 1 and result.pending == 1


@pytest.mark.asyncio
async def test_removing_scam_prevents_staff_command(database):
    settings, record_id, relay, client = await prepare(database)
    async with database() as session:
        record = await session.get(ScamRecord, record_id)
        record.status = "REMOVED"
        await session.commit()
    set_relay(relay)
    result = await process_scam_bans(Bot(client), settings, database, record_id)
    assert result.succeeded == 0 and not client.calls


@pytest.mark.asyncio
async def test_durable_staff_cooldown_survives_relay_restart(database):
    settings, record_id, relay, client = await prepare(database)
    async with database() as session:
        service = GroupService(settings, session)
        jobs = await service.claim_bans()
        assert await relay.dispatch(service, jobs[0])
        restarted = GroupHelpRelay(settings, database, Client(), relay.staff)
        assert await restarted.dispatch(service, jobs[1])
        assert not restarted.client.calls


@pytest.mark.asyncio
async def test_mtproto_flood_wait_keeps_existing_pending_semantics(database):
    from telethon.errors import FloodWaitError

    settings, record_id, relay, client = await prepare(database)
    client.error = FloodWaitError(request=None, capture=120)
    async with database() as session:
        service = GroupService(settings, session)
        job = (await service.claim_bans())[0]
        with pytest.raises(TelegramRetryAfter) as caught:
            await relay.dispatch(service, job)
        assert caught.value.retry_after >= 119


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o666])
def test_credentials_with_public_or_group_permissions_are_rejected(tmp_path, mode):
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    (directory / "api.json").write_text('{"api_id":12345,"api_hash":"' + "a" * 32 + '"}')
    (directory / "account.session").touch(mode=0o600)
    (directory / "api.json").chmod(mode)
    with pytest.raises(ValueError):
        private_configuration(directory)


def test_disabled_configuration_preserves_existing_bot_api_default(settings):
    assert not settings.group_help_enabled
    with pytest.raises(ValueError):
        Settings(bot_token="123456:TEST_TOKEN", group_help_enabled=True, _env_file=None)


@pytest.mark.asyncio
async def test_activation_only_requeues_failed_active_staff_jobs_once(database):
    settings, record_id, relay, client = await prepare(database)
    async with database() as session:
        jobs = list((await session.scalars(select(BanAction))).all())
        for job in jobs:
            job.status, job.attempts, job.result_type = "FAILED", 8, "TelegramBadRequest"
        jobs[1].result_type = "TelegramRetryAfter"
        jobs[1].next_attempt_at = now() + timedelta(minutes=10)
        await session.commit()
    await relay.activate(42)
    async with database() as session:
        jobs = list(
            (await session.scalars(select(BanAction).order_by(BanAction.chat_id.desc()))).all()
        )
        assert (jobs[0].status, jobs[0].attempts) == ("PENDING", 0)
        assert (jobs[1].status, jobs[1].attempts) == ("FAILED", 8)
        jobs[0].status, jobs[0].attempts = "FAILED", 8
        await session.commit()
    await relay.activate(42)
    async with database() as session:
        assert all(j.status == "FAILED" for j in (await session.scalars(select(BanAction))).all())


@pytest.mark.asyncio
async def test_flood_wait_persists_across_restarts(database):
    from telethon.errors import FloodWaitError

    settings, record_id, relay, client = await prepare(database)
    await relay._note_flood(FloodWaitError(request=None, capture=120))
    restarted = GroupHelpRelay(settings, database, Client(), relay.staff)
    await restarted.activate(42)
    assert restarted.flood_until >= now() + timedelta(seconds=119)


@pytest.mark.asyncio
async def test_background_resolution_skips_exhausted_records(database):
    settings, first_id, relay, client = await prepare(database, "@exhausted")
    async with database() as session:
        core = Service(settings, session)
        first = await core.repo.scam_by_id(first_id)
        session.add(
            AuditEvent(
                actor_id=900,
                action="mtproto_identity_attempt",
                target_id=first.target_id,
                details={"record_id": first_id, "attempt": 8},
                created_at=now() - timedelta(minutes=3),
            )
        )
        await session.commit()
        second = await core.add_scam(900, "@scammer")
        second_id = second.id
    assert await relay.resolve_pending() == 1
    assert client.calls[0].username == "scammer"
    async with database() as session:
        assert (
            await Service(settings, session).repo.scam_by_id(second_id)
        ).target.telegram_id == 22

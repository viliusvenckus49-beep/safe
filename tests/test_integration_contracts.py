"""Opt-in PostgreSQL concurrency and Redis persistence contracts.

Only use dedicated test services; PostgreSQL schemas and Redis prefixes are isolated.
"""

import asyncio
import os
from uuid import uuid4

import pytest
import pytest_asyncio
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.fsm.storage.redis import RedisEventIsolation, RedisStorage
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import (
    AuditEvent,
    Base,
    ModerationAction,
    Report,
    ReputationRequest,
    ScamRecord,
    User,
)
from app.services import DomainError, Service


@pytest_asyncio.fixture
async def postgres_contract():
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL for isolated PostgreSQL integration checks")
    schema = "safecheck_test_" + uuid4().hex
    admin = create_async_engine(url)
    engine = None
    try:
        async with admin.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        settings = Settings(
            bot_token="123456:TEST_ONLY",
            admin_ids="900,901",
            database_url=url,
            environment="development",
            rep_cooldown_seconds=0,
            report_cooldown_seconds=0,
            _env_file=None,
        )
        yield async_sessionmaker(engine, expire_on_commit=False), settings
    finally:
        if engine:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin.dispose()


@pytest.mark.asyncio
async def test_postgres_reciprocal_votes_cannot_both_succeed(postgres_contract):
    factory, settings = postgres_contract
    async with factory() as session:
        svc = Service(settings, session)
        await svc.observe(1, "first_user", "First")
        await svc.observe(2, "second_user", "Second")

    async def vote(actor, target):
        async with factory() as session:
            try:
                await Service(settings, session).vote(
                    actor, str(target), 1, None, f"pair:{actor}", comment="Test comment"
                )
                return "ok"
            except DomainError as error:
                return error.code

    results = await asyncio.wait_for(asyncio.gather(vote(1, 2), vote(2, 1)), timeout=30)
    assert sorted(results) == ["ok", "reciprocal_rep"]
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 1


@pytest.mark.asyncio
async def test_postgres_parallel_report_approval_is_idempotent(postgres_contract):
    factory, settings = postgres_contract
    async with factory() as session:
        report = await Service(settings, session).submit_report(
            1, "2", "Patvirtinti įrodymai", [], "one"
        )
        reference = report.reference

    async def approve(actor):
        async with factory() as session:
            return (await Service(settings, session).moderate(actor, reference, True)).status

    results = await asyncio.wait_for(asyncio.gather(*(approve(900 + i % 2) for i in range(8))), 30)
    assert results == ["APPROVED"] * 8
    async with factory() as session:
        for model in (ScamRecord, ModerationAction, AuditEvent):
            assert await session.scalar(select(func.count()).select_from(model)) == 1


@pytest.mark.asyncio
async def test_postgres_parallel_first_identity_and_username_ownership(postgres_contract):
    factory, settings = postgres_contract

    async def observe(tg_id, username):
        async with factory() as session:
            return (await Service(settings, session).observe(tg_id, username, "Name")).id

    same = await asyncio.wait_for(asyncio.gather(*(observe(1, "first_user") for _ in range(8))), 30)
    assert len(set(same)) == 1
    owners = await asyncio.wait_for(
        asyncio.gather(observe(1, "same_name"), observe(2, "same_name")), 30
    )
    assert len(set(owners)) == 2
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 2
        assert (
            await session.scalar(
                select(func.count()).select_from(User).where(User.username == "same_name")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_postgres_parallel_report_submit_returns_one_reference(postgres_contract):
    factory, settings = postgres_contract

    async def submit():
        async with factory() as session:
            return (
                await Service(settings, session).submit_report(
                    1,
                    "2",
                    "Prarastas mokėjimas",
                    [{"kind": "photo", "file_id": "test_photo"}],
                    "same-draft",
                )
            ).reference

    references = await asyncio.wait_for(asyncio.gather(*(submit() for _ in range(8))), 30)
    assert len(set(references)) == 1
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Report)) == 1


@pytest.mark.asyncio
async def test_redis_fsm_survives_client_restart_and_isolates_operations():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("Set TEST_REDIS_URL for isolated Redis integration checks")
    keys = DefaultKeyBuilder(prefix="safecheck_test_" + uuid4().hex, with_bot_id=True)
    key = StorageKey(bot_id=123, chat_id=1, user_id=1)
    first = RedisStorage.from_url(url, key_builder=keys, state_ttl=60, data_ttl=60)
    second = RedisStorage.from_url(url, key_builder=keys, state_ttl=60, data_ttl=60)
    isolation = RedisEventIsolation(redis=second.redis, key_builder=keys)
    try:
        await first.set_state(key, "ReportFlow:reason")
        await first.set_data(key, {"nonce": "private-draft", "counter": 0})
        await first.close()
        assert await second.get_state(key) == "ReportFlow:reason"
        assert (await second.get_data(key))["nonce"] == "private-draft"

        async def increment():
            async with isolation.lock(key):
                data = await second.get_data(key)
                await asyncio.sleep(0.01)
                data["counter"] += 1
                await second.set_data(key, data)

        await asyncio.wait_for(asyncio.gather(*(increment() for _ in range(8))), 30)
        assert (await second.get_data(key))["counter"] == 8
    finally:
        await second.redis.delete(keys.build(key, "state"), keys.build(key, "data"))
        await first.close()
        await isolation.close()
        await second.close()


@pytest.mark.asyncio
async def test_postgres_parallel_reputation_approval_once(postgres_contract):
    factory, settings = postgres_contract
    async with factory() as session:
        result = await Service(settings, session).vote(
            1, "2", 1, None, "pending-rep", comment="Test comment"
        )
        reference = result["reputation_request"].reference

    async def approve(actor):
        async with factory() as session:
            return (
                await Service(settings, session).moderate_reputation(actor, reference, True)
            ).status

    assert (
        await asyncio.wait_for(asyncio.gather(*(approve(900 + i % 2) for i in range(8))), 30)
        == ["APPROVED"] * 8
    )
    async with factory() as session:
        profile = await Service(settings, session).profile("2")
        assert (profile["score"], profile["positive"], profile["negative"]) == (1, 1, 0)
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1


@pytest.mark.asyncio
async def test_postgres_parallel_reputation_adjust_reset_top_retries(postgres_contract):
    from app.models import ReputationAdjustment, TopVisibilityAction

    factory, settings = postgres_contract
    reason = "Administratoriaus sprendimas"

    async def action(method, *args):
        async with factory() as session:
            return await getattr(Service(settings, session), method)(900, "2", *args)

    await asyncio.wait_for(
        asyncio.gather(*(action("admin_adjust_rep", 7, reason, "same-adjust") for _ in range(8))),
        30,
    )
    await asyncio.wait_for(
        asyncio.gather(*(action("admin_reset_rep", reason, "same-reset") for _ in range(8))), 30
    )
    await asyncio.wait_for(
        asyncio.gather(*(action("set_top_visibility", True, reason, "same-top") for _ in range(8))),
        30,
    )
    async with factory() as session:
        svc = Service(settings, session)
        profile = await svc.profile("2")
        assert (profile["score"], profile["positive"], profile["negative"]) == (0, 0, 0)
        assert await session.scalar(select(func.count()).select_from(ReputationAdjustment)) == 2
        assert await session.scalar(select(func.count()).select_from(TopVisibilityAction)) == 1
        assert [row["user"].telegram_id for row in await svc.leaderboard()] == [2]


@pytest.mark.asyncio
async def test_postgres_reset_vs_approval_serializes_coherently(postgres_contract):
    factory, settings = postgres_contract
    async with factory() as session:
        svc = Service(settings, session)
        await svc.admin_adjust_rep(900, "2", 5, "Pradinis administratoriaus įrašas", "initial")
        result = await svc.vote(1, "2", 1, None, "pending", comment="Test comment")
        reference = result["reputation_request"].reference

    async def approve():
        async with factory() as session:
            await Service(settings, session).moderate_reputation(900, reference, True)

    async def reset():
        async with factory() as session:
            await Service(settings, session).admin_reset_rep(
                900, "2", "Reputacijos atkūrimo sprendimas", "reset"
            )

    async def observe():
        async with factory() as session:
            svc = Service(settings, session)
            for _ in range(20):
                profile = await svc.profile("2")
                assert profile["positive"] >= 0 and profile["negative"] >= 0
                assert profile["score"] == profile["positive"] - profile["negative"]

    await asyncio.wait_for(asyncio.gather(approve(), reset(), observe()), 30)
    async with factory() as session:
        assert (await Service(settings, session).profile("2"))["score"] in (0, 1)


@pytest.mark.asyncio
async def test_postgres_concurrent_administrator_grants_are_idempotent(postgres_contract):
    import asyncio

    from app.admin_services import AdminService
    from app.models import AdministratorChange, AuditEvent

    sessions, settings = postgres_contract
    settings.group_owner_id = 900

    async def grant():
        async with sessions() as session:
            return await AdminService(settings, session).change(900, "42", True, "a" * 32)

    results = await asyncio.gather(grant(), grant(), grant())
    assert sorted(results) == [False, False, True]
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(AdministratorChange)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        await AdminService(settings, session).change(900, "42", False, "b" * 32)
    async with sessions() as session:
        assert not await AdminService(settings, session).is_admin(42)
        assert not await AdminService(settings, session).change(900, "42", True, "a" * 32)
        assert not await AdminService(settings, session).is_admin(42)


@pytest.mark.asyncio
async def test_postgres_scam_add_and_member_observation_keep_export_filtered(postgres_contract):
    import asyncio

    from app.group_services import GroupService

    sessions, settings = postgres_contract
    settings.group_owner_id = 900
    async with sessions() as session:
        await GroupService(settings, session).register_group(900, -100, "Group", True)

    async def observe():
        async with sessions() as session:
            await GroupService(settings, session).observe_member(-100, 42, "example", "Person")

    async def add():
        async with sessions() as session:
            await Service(settings, session).add_scam(900, "42")

    await asyncio.gather(observe(), add())
    async with sessions() as session:
        assert await GroupService(settings, session).export_members(900, -100) == []
        users, _ = await Service(settings, session).users(900, 0)
        assert 42 not in [user.telegram_id for user in users]


@pytest.mark.asyncio
async def test_postgres_trusted_grant_retry_is_serialized(postgres_contract):
    sessions, settings = postgres_contract

    async def grant():
        async with sessions() as session:
            return await Service(settings, session).set_trusted(900, "42", True, "same-key")

    results = await asyncio.gather(grant(), grant(), grant())
    assert all(result["trusted"] for result in results)
    from app.models import TrustedAction

    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(TrustedAction)) == 1
        service = Service(settings, session)
        await service.set_trusted(900, "42", False, "remove")
        assert not (await service.set_trusted(900, "42", True, "same-key"))["trusted"]


@pytest.mark.asyncio
async def test_postgres_directory_prefers_observed_id_without_transferring_history(
    postgres_contract,
):
    factory, settings = postgres_contract
    async with factory() as session:
        service = Service(settings, session)
        unresolved = await service.resolve("@example")
        await service.set_trusted(900, "@example", True, "historic-trusted")
        unresolved_id = unresolved.id
    async with factory() as session:
        service = Service(settings, session)
        await service.observe(42, "example", "Observed name")
        rows, total = await service.users(900, 0)
        assert total == 1 and [row.telegram_id for row in rows] == [42]
        assert await session.get(User, unresolved_id) is not None
        assert not (await service.profile("42"))["trusted"]
        assert (await service.profile(f"u:{unresolved_id}"))["trusted"]


@pytest.mark.asyncio
async def test_postgres_parallel_trusted_management_removal_is_atomic(postgres_contract):
    from app.models import TopVisibilityAction, TrustedAction
    from app.trusted_management import TrustedManagement

    factory, settings = postgres_contract
    async with factory() as session:
        service = Service(settings, session)
        await service.set_trusted(900, "42", True, "grant")
        await service.set_top_visibility(900, "42", True, "Reviewed trusted user", "top")
        target_id = (await service.resolve("42")).id

    async def remove():
        async with factory() as session:
            return await TrustedManagement(Service(settings, session)).revoke(
                900, target_id, "a" * 32
            )

    results = await asyncio.wait_for(asyncio.gather(*(remove() for _ in range(6))), 30)
    assert all(not result["trusted"] for result in results)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(TrustedAction)) == 2
        assert await session.scalar(select(func.count()).select_from(TopVisibilityAction)) == 2


@pytest.mark.asyncio
async def test_postgres_scam_identity_double_confirmation_and_ban_outbox(postgres_contract):
    from app.models import BanAction, ManagedGroup
    from app.scam_management import ScamManagement

    factory, settings = postgres_contract
    async with factory() as session:
        core = Service(settings, session)
        record = await core.add_scam(900, "@unknownscam")
        record_id = record.id
        session.add(
            ManagedGroup(
                chat_id=-456,
                title="Approved group",
                approved=True,
                enabled=True,
                can_restrict_members=True,
            )
        )
        await session.commit()

    async def confirm():
        async with factory() as session:
            await ScamManagement(Service(settings, session)).supplement(
                900, record_id, "id", "42", "a" * 32
            )

    await asyncio.wait_for(asyncio.gather(confirm(), confirm()), timeout=30)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(BanAction)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "scam_identity_supplemented")
            )
            == 1
        )
        assert (
            await ScamManagement(Service(settings, session)).detail(900, record_id)
        ).target.telegram_id == 42


@pytest.mark.asyncio
async def test_postgres_scam_identity_competing_records_conflict(postgres_contract):
    from app.scam_management import ScamManagement

    factory, settings = postgres_contract
    async with factory() as session:
        core = Service(settings, session)
        first = await core.add_scam(900, "@firstscam")
        second = await core.add_scam(900, "@secondscam")
        ids = first.id, second.id

    async def bind(record_id, nonce):
        async with factory() as session:
            try:
                await ScamManagement(Service(settings, session)).supplement(
                    900, record_id, "id", "42", nonce
                )
                return "ok"
            except DomainError as error:
                return error.code

    results = await asyncio.wait_for(
        asyncio.gather(bind(ids[0], "b" * 32), bind(ids[1], "c" * 32)), timeout=30
    )
    assert sorted(results) == ["duplicate_scam", "ok"]

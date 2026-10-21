"""Whole-process supervision and cleanup contracts; no real Telegram traffic."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from test_integration_contracts import postgres_contract as postgres_contract

from app import main
from app.ads_publication import PublicationService
from app.bot.ads_publisher import AdsPublisher, ads_worker
from app.health import RuntimeHealth, healthy, snapshot


async def prepared(database, settings, monkeypatch, tmp_path):
    engine = database.kw["bind"]
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        )
        await conn.execute(text("INSERT INTO alembic_version VALUES ('0019')"))
    monkeypatch.setattr(main, "create_database", lambda _: (engine, database))
    monkeypatch.setattr(main, "configure_logging", lambda _: None)
    monkeypatch.setenv("HEALTH_PATH", str(tmp_path / "health.json"))
    closed = []

    class Storage:
        async def close(self):
            closed.append("storage")

    class Isolation:
        async def close(self):
            closed.append("isolation")

    class Bot:
        id = 123456
        session = None

        def __init__(self, *args, **kwargs):
            self.session = self

        async def close(self):
            closed.append("bot")

        async def set_my_commands(self, *args, **kwargs):
            pass

        async def get_webhook_info(self):
            return SimpleNamespace(url="")

    class Dispatcher:
        async def stop_polling(self):
            raise RuntimeError("Polling test double has no children")

        def __init__(self, **kwargs):
            pass

        def include_router(self, router):
            pass

        def errors(self):
            return lambda f: f

        async def start_polling(self, *args, **kwargs):
            try:
                await asyncio.Future()
            finally:
                closed.append("polling")

    async def advertising(*args, **kwargs):
        try:
            await asyncio.Future()
        finally:
            closed.append("ads")

    monkeypatch.setattr(main, "ads_worker", advertising)

    async def commands(*args, **kwargs):
        pass

    async def worker(*args, **kwargs):
        try:
            await asyncio.Future()
        finally:
            closed.append("group")

    monkeypatch.setattr(main, "MemoryStorage", Storage)
    monkeypatch.setattr(main, "SimpleEventIsolation", Isolation)
    monkeypatch.setattr(main, "Bot", Bot)
    monkeypatch.setattr(main, "Dispatcher", Dispatcher)
    monkeypatch.setattr(main, "group_commands", commands)
    monkeypatch.setattr(main, "user_commands", commands)
    monkeypatch.setattr(main, "group_worker", worker)
    monkeypatch.setattr(main, "telegram_session", lambda *a, **kw: None)
    return closed


async def test_ads_startup_failure_stops_polling_and_cleans_every_resource(
    database, settings, monkeypatch, tmp_path
):
    closed = await prepared(database, settings, monkeypatch, tmp_path)

    async def failed(*args, **kwargs):
        await asyncio.sleep(0)
        raise RuntimeError("isolated ADS initial planning failure")

    monkeypatch.setattr(main, "ads_worker", failed)
    with pytest.raises(RuntimeError, match="isolated ADS initial planning failure"):
        await asyncio.wait_for(main.serve(settings), 2)
    assert {"polling", "group", "storage", "isolation", "bot"} <= set(closed)


def test_ads_failure_cannot_be_masked_by_live_poll_and_group_worker(tmp_path):
    path = tmp_path / "health.json"
    health = RuntimeHealth(path, advertising=True)
    health.polling()
    health.worker()
    assert not healthy(path)
    for phase in ("plan", "dispatch", "coord"):
        health.advertising(phase)
    assert healthy(path)
    health.close()


@pytest.mark.parametrize("task_name", ["group_worker", "ads_worker"])
@pytest.mark.parametrize("outcome", ["return", "exception", "cancel"])
async def test_required_worker_exit_is_fatal_and_closes_peers(
    database, settings, monkeypatch, tmp_path, task_name, outcome
):
    closed = await prepared(database, settings, monkeypatch, tmp_path)

    async def stopped(*args, **kwargs):
        await asyncio.sleep(0)
        if outcome == "exception":
            raise ValueError("primary task failure")
        if outcome == "cancel":
            raise asyncio.CancelledError()

    monkeypatch.setattr(main, task_name, stopped)
    with pytest.raises((RuntimeError, ValueError)):
        await asyncio.wait_for(main.serve(settings), 2)
    assert {"polling", "storage", "isolation", "bot"} <= set(closed)
    assert not (tmp_path / "health.json").exists()


@pytest.mark.parametrize("cleanup_failure", [False, True])
async def test_normal_polling_shutdown_joins_workers_and_attempts_every_cleanup(
    database, settings, monkeypatch, tmp_path, cleanup_failure
):
    closed = await prepared(database, settings, monkeypatch, tmp_path)

    async def polling(*args, **kwargs):
        await asyncio.sleep(0)
        closed.append("polling")

    async def broken_close(self):
        closed.append("isolation")
        raise ConnectionError("private resource details")

    monkeypatch.setattr(main.Dispatcher, "start_polling", polling)
    if cleanup_failure:
        monkeypatch.setattr(main.SimpleEventIsolation, "close", broken_close)
        with pytest.raises(RuntimeError, match="cleanup"):
            await asyncio.wait_for(main.serve(settings), 2)
    else:
        await asyncio.wait_for(main.serve(settings), 2)
    assert {"polling", "group", "ads", "isolation", "storage", "bot"} <= set(closed)


async def test_shutdown_does_not_mask_primary_worker_failure(
    database, settings, monkeypatch, tmp_path
):
    closed = await prepared(database, settings, monkeypatch, tmp_path)

    async def failed(*args, **kwargs):
        raise ValueError("original worker error")

    async def broken_close(self):
        closed.append("storage")
        raise OSError("private close details")

    monkeypatch.setattr(main, "ads_worker", failed)
    monkeypatch.setattr(main.MemoryStorage, "close", broken_close)
    with pytest.raises(ValueError, match="original worker error"):
        await asyncio.wait_for(main.serve(settings), 2)
    assert {"storage", "bot", "isolation"} <= set(closed)


@pytest.mark.parametrize("phase", ["plan", "dispatch", "coord"])
def test_each_ads_signal_must_remain_fresh(tmp_path, monkeypatch, phase):
    current = [1000.0]
    monkeypatch.setattr("app.health.time.time", lambda: current[0])
    path = tmp_path / "health.json"
    health = RuntimeHealth(path, advertising=True)
    for item in ("plan", "dispatch", "coord"):
        health.advertising(item)
    current[0] += 181
    health.polling()
    health.worker()
    for item in ("plan", "dispatch", "coord"):
        if item != phase:
            health.advertising(item)
    assert not healthy(path)
    assert snapshot(path)["ads_" + phase] == 181
    health.advertising(phase)
    assert healthy(path)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("child", ["planning", "coordination"])
@pytest.mark.parametrize("outcome", ["return", "exception", "cancel"])
async def test_ads_child_cannot_silently_die(postgres_contract, monkeypatch, child, outcome):
    sessions, settings = postgres_contract

    async def failed(self):
        if outcome == "exception":
            raise ValueError("child failed")
        if outcome == "cancel":
            raise asyncio.CancelledError()

    monkeypatch.setattr(AdsPublisher, child, failed)
    with pytest.raises((RuntimeError, ValueError)):
        await asyncio.wait_for(ads_worker(SimpleNamespace(id=777), settings, sessions), 2)


async def test_failed_planning_has_no_success_heartbeat(database, settings, monkeypatch):
    attempted = asyncio.Event()
    signals = []

    async def failed(*args, **kwargs):
        attempted.set()
        raise ConnectionError("private SQL connection details")

    monkeypatch.setattr(PublicationService, "enqueue", failed)
    publisher = AdsPublisher(None, settings, database, signals.append)
    task = asyncio.create_task(publisher.planning())
    try:
        await asyncio.wait_for(attempted.wait(), 2)
        await asyncio.sleep(0)
        assert signals == []
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_packaged_process_contract_uses_real_sql_and_joins_workers(
    database, settings, monkeypatch, tmp_path
):
    # Real ADS planner/coordinator/dispatcher sessions, fake Telegram transport.
    closed = await prepared(database, settings, monkeypatch, tmp_path)
    monkeypatch.setattr(main, "ads_worker", ads_worker)
    ready = asyncio.Event()
    original = AdsPublisher.pool_tick

    async def observe(self):
        await original(self)
        if healthy(tmp_path / "health.json"):
            ready.set()

    monkeypatch.setattr(AdsPublisher, "pool_tick", observe)

    signals = {}

    def capture_health(*args, **kwargs):
        signals["health"] = RuntimeHealth(*args, **kwargs)
        return signals["health"]

    monkeypatch.setattr(main, "RuntimeHealth", capture_health)

    async def polling(*args, **kwargs):
        while not ready.is_set():
            signals["health"].polling()
            await asyncio.sleep(0.01)
        closed.append("polling")

    async def group(*args, on_progress, **kwargs):
        while True:
            on_progress()
            await asyncio.sleep(0.01)

    monkeypatch.setattr(main.Dispatcher, "start_polling", polling)
    monkeypatch.setattr(main, "group_worker", group)
    await asyncio.wait_for(main.serve(settings), 5)
    assert {"polling", "storage", "isolation", "bot"} <= set(closed)
    assert not (tmp_path / "health.json").exists()


async def test_inflight_update_finishes_before_storage_and_database_close(
    database, settings, monkeypatch, tmp_path
):
    closed = await prepared(database, settings, monkeypatch, tmp_path)

    async def update():
        await asyncio.sleep(0.02)
        closed.append("update")

    async def polling(self, *args, **kwargs):
        self._handle_update_tasks = {asyncio.create_task(update())}
        await asyncio.sleep(0)

    monkeypatch.setattr(main.Dispatcher, "start_polling", polling)
    await asyncio.wait_for(main.serve(settings), 2)
    assert closed.index("update") < closed.index("storage") < closed.index("bot")


async def test_native_aiogram_polling_children_are_joined(monkeypatch):
    from run_runtime_smoke import smoke

    # Register restoration of the standalone smoke's deliberate injection.
    monkeypatch.setattr(main, "telegram_session", main.telegram_session)
    monkeypatch.setenv("HEALTH_PATH", "/tmp/unused-stage35-health.json")
    await smoke()

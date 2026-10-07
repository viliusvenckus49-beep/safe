import json
from pathlib import Path

import pytest

from app.health import RuntimeHealth, healthy


def test_both_polling_and_worker_required_and_expiry(tmp_path, monkeypatch):
    monkeypatch.setattr("app.health.time.time", lambda: 1000)
    path = tmp_path / "health.json"
    health = RuntimeHealth(path)
    assert not healthy(path, current=1000)
    health.polling()
    assert not healthy(path, current=1000)
    health.worker()
    assert healthy(path, current=1000)
    assert path.stat().st_mode & 0o777 == 0o600
    assert not healthy(path, current=1181)
    assert not healthy(path, current=999)
    health.close()
    assert not healthy(path)


@pytest.mark.parametrize(
    "content",
    [
        "{}",
        "[]",
        "null",
        "bad",
        '{"poll_at":true,"worker_at":true}',
        '{"poll_at":NaN,"worker_at":1}',
    ],
)
def test_malformed_status_is_unhealthy(tmp_path, content):
    path = tmp_path / "health.json"
    path.write_text(content)
    assert not healthy(path, current=1000)


@pytest.mark.parametrize("age", [0, -1, float("nan"), float("inf")])
def test_invalid_max_age_rejects(tmp_path, age):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"poll_at": 1000, "worker_at": 1000}))
    assert not healthy(path, age, current=1000)


def test_missing_status_unhealthy(tmp_path):
    assert not healthy(Path(tmp_path / "missing.json"))


@pytest.mark.asyncio
async def test_failed_telegram_poll_does_not_refresh_heartbeat(settings):
    from aiogram.methods import GetUpdates

    from app.bot.session import telegram_session

    progress = []
    session = telegram_session(settings, on_poll=lambda: progress.append("poll"))
    telemetry = session.middleware._middlewares[0]

    async def success(bot, method):
        return []

    async def failure(bot, method):
        raise ConnectionError("private request details")

    await telemetry(success, None, GetUpdates())
    assert progress == ["poll"]
    with pytest.raises(ConnectionError):
        await telemetry(failure, None, GetUpdates())
    assert progress == ["poll"]
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_worker_heartbeat_requires_successful_cycle(settings, monkeypatch, failure):
    import asyncio

    from app.bot import group_runtime

    progress = []

    async def cycle(*args):
        if failure:
            raise ConnectionError("private database details")
        return 0

    async def stop(*args):
        raise asyncio.CancelledError()

    monkeypatch.setattr(group_runtime, "process_group_jobs", cycle)
    monkeypatch.setattr(group_runtime.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await group_runtime.group_worker(
            None, settings, None, on_progress=lambda: progress.append("worker")
        )
    assert progress == ([] if failure else ["worker"])

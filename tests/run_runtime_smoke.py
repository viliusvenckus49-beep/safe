"""Packaged whole-process smoke. No network, runtime secrets or production data."""

import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram.client.session.base import BaseSession
from aiogram.types import User, WebhookInfo
from sqlalchemy import text

from app import main
from app.config import Settings
from app.db import create_database
from app.health import healthy, snapshot
from app.models import Base


class TelegramDouble(BaseSession):
    def __init__(self, on_poll):
        super().__init__()
        self.on_poll = on_poll
        self.closed = False

    async def close(self):
        self.closed = True

    async def stream_content(self, *args, **kwargs):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        kind = type(method).__name__
        if kind == "GetMe":
            return User(id=123456, is_bot=True, first_name="Isolated test")
        if kind == "GetWebhookInfo":
            return WebhookInfo(url="", pending_update_count=0, has_custom_certificate=False)
        if kind == "SetMyCommands":
            return True
        if kind == "GetUpdates":
            await asyncio.sleep(0.05)
            self.on_poll()
            return []
        raise AssertionError("Unexpected network operation: " + kind)


async def smoke():
    directory = TemporaryDirectory(prefix="safecheck-runtime-smoke-")
    path = Path(directory.name) / "health.json"
    os.environ["HEALTH_PATH"] = str(path)
    url = f"sqlite+aiosqlite:///{directory.name}/runtime.db"
    engine, _ = create_database(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text("CREATE TABLE alembic_version(version_num VARCHAR(32))"))
        await connection.execute(text("INSERT INTO alembic_version VALUES ('0018')"))
    await engine.dispose()
    transport = []

    def session(settings, *, on_poll, **kwargs):
        value = TelegramDouble(on_poll)
        transport.append(value)
        return value

    main.telegram_session = session
    settings = Settings(
        bot_token="123456:ISOLATED_ONLY", admin_ids="900", database_url=url, _env_file=None
    )
    task = asyncio.create_task(main.serve(settings))
    try:
        async with asyncio.timeout(15):
            while not healthy(path):
                if task.done():
                    task.result()
                    raise AssertionError("Bot exited before becoming healthy")
                await asyncio.sleep(0.05)
        state = snapshot(path)
        assert all(state[key] is not None for key in ("ads_plan", "ads_dispatch", "ads_coord"))
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert not path.exists() and transport[0].closed
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert not pending, pending
    directory.cleanup()
    print("Packaged bot, group worker and ADS loops: startup/progress/shutdown OK; no network")


if __name__ == "__main__":
    asyncio.run(smoke())

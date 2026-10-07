import pytest
import pytest_asyncio

from app.config import Settings
from app.db import create_database
from app.models import Base


@pytest.fixture
def settings():
    return Settings(bot_token="123456:TEST_ONLY_NOT_A_REAL_TOKEN", admin_ids="900", _env_file=None)


@pytest_asyncio.fixture
async def database(tmp_path):
    engine, factory = create_database(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()

"""Read-only infrastructure monitor aggregates, with no user data in output."""

import asyncio
import json
import sys

from app.config import Settings
from app.db import create_database
from app.repositories import Repository


async def status(settings: Settings) -> dict[str, int]:
    engine, sessions = create_database(settings.database_url)
    try:
        async with sessions() as session:
            return await Repository(session).operational_stats()
    finally:
        await engine.dispose()


def run() -> None:
    try:
        print(json.dumps(asyncio.run(status(Settings()))))
    except Exception:
        print("SAFECheck operational status unavailable", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    run()

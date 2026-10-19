"""10 / 100 / 1000 synthetic PostgreSQL groups; zero real Telegram API requests.

Measures SQL + worker/request-double capacity with rate budgets disabled. This is
not Telegram throughput; normal defaults enforce 20 msg/s globally and 20/min/chat.
"""

import asyncio
import json
import os
import time

import pytest
from sqlalchemy import func, insert, select
from test_ads_publication import INSTANT, configured
from test_ads_publication import clock as clock
from test_integration_contracts import postgres_contract as postgres_contract

from app.ads_publication import PublicationService
from app.bot.ads_publisher import AdsPublisher
from app.models import AdPublicationAttempt, AdSchedule, ManagedGroup


@pytest.mark.parametrize("count", [10, 100, 1000])
async def test_synthetic_multi_group_capacity(postgres_contract, clock, count):
    sessions, settings = postgres_contract
    cid, _, bot, _, _ = await configured(sessions, settings, clock)
    async with sessions() as session:
        await session.execute(
            insert(ManagedGroup),
            [
                {"chat_id": -100 - i, "title": f"Synthetic {i}", "approved": True, "enabled": True}
                for i in range(1, count)
            ],
        )
        await session.execute(
            insert(AdSchedule),
            [
                {
                    "campaign_id": cid,
                    "chat_id": -100 - i,
                    "authorized_by": 900,
                    "status": "ACTIVE",
                    "interval_seconds": 1800,
                    "start_at": INSTANT,
                    "next_run_at": INSTANT,
                    "override_fields": [],
                }
                for i in range(1, count)
            ],
        )
        await session.commit()
    started = time.perf_counter()
    scheduled = 0
    while scheduled < count:
        async with sessions() as session:
            n = await PublicationService(settings, session).enqueue(limit=128)
        assert n > 0
        scheduled += n
    enqueued = time.perf_counter()
    publisher = AdsPublisher(bot, settings, sessions)
    publisher.background = True

    async def drain():
        while True:
            await publisher.pool_tick()
            if len(bot.calls) >= count and not publisher.inflight:
                return

    try:
        await asyncio.wait_for(drain(), 360)
    finally:
        for task in publisher.inflight:
            task.cancel()
        await asyncio.gather(*publisher.inflight, return_exceptions=True)
    completed = time.perf_counter()
    async with sessions() as session:
        sent = await session.scalar(
            select(func.count())
            .select_from(AdPublicationAttempt)
            .where(AdPublicationAttempt.status == "SENT")
        )
        assert sent == count
        assert await session.scalar(select(func.count()).select_from(AdPublicationAttempt)) == count
    assert len(bot.calls) == count and len({args["chat_id"] for _, args in bot.calls}) == count
    print(
        "ADS33_LOAD "
        + json.dumps(
            {
                "groups": count,
                "pool": settings.ads_worker_concurrency,
                "logical_cpus": os.cpu_count(),
                "enqueue_s": round(enqueued - started, 3),
                "delivery_s": round(completed - enqueued, 3),
                "total_s": round(completed - started, 3),
                "publications_per_s": round(count / (completed - enqueued), 2),
                "telegram": "double; rate budgets disabled; not live delivery",
            }
        )
    )

"""Large-network profile lookup must bound Telegram concurrency and release SQL."""

import asyncio
from types import SimpleNamespace

from test_integration_contracts import postgres_contract as postgres_contract

from app.bot.redsafe_profile import profile_data
from app.models import ManagedGroup
from app.services import Service


async def test_profile_membership_lookup_is_bounded_and_does_not_hold_sql(postgres_contract):
    sessions, settings = postgres_contract
    async with sessions() as session:
        service = Service(settings, session)
        await service.observe(42, "customer", "Profile")
        session.add_all(
            [
                ManagedGroup(chat_id=-100 - i, title=f"QA {i}", approved=True, enabled=True)
                for i in range(100)
            ]
        )
        await session.commit()
        active = peak = calls = 0
        reached = asyncio.Event()
        release = asyncio.Event()

        class Bot:
            async def get_chat_member(self, chat, user):
                nonlocal active, peak, calls
                assert not session.in_transaction()
                active += 1
                peak = max(peak, active)
                calls += 1
                if active == 8:
                    reached.set()
                try:
                    await release.wait()
                    return SimpleNamespace(status="member")
                finally:
                    active -= 1

        task = asyncio.create_task(profile_data(service, Bot(), "42"))
        try:
            await asyncio.wait_for(reached.wait(), 5)
            await asyncio.sleep(0.01)
            assert peak <= 8
            release.set()
            result = await asyncio.wait_for(task, 5)
            assert result["groups"] == calls == 100
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_concurrent_profiles_share_bot_lookup_capacity(postgres_contract):
    sessions, settings = postgres_contract
    async with sessions() as session:
        service = Service(settings, session)
        await service.observe(42, "customer", "Profile")
        session.add_all(
            [
                ManagedGroup(chat_id=-100 - i, title=f"QA {i}", approved=True, enabled=True)
                for i in range(30)
            ]
        )
        await session.commit()
    active = peak = calls = 0
    reached, release = asyncio.Event(), asyncio.Event()

    class Bot:
        async def get_chat_member(self, chat, user):
            nonlocal active, peak, calls
            active += 1
            calls += 1
            peak = max(peak, active)
            if active == 8:
                reached.set()
            try:
                await release.wait()
                return SimpleNamespace(status="member")
            finally:
                active -= 1

    bot = Bot()

    async def request():
        async with sessions() as session:
            return await profile_data(Service(settings, session), bot, "42")

    tasks = [asyncio.create_task(request()) for _ in range(3)]
    try:
        await asyncio.wait_for(reached.wait(), 5)
        await asyncio.sleep(0.05)
        assert peak <= 8
        release.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert [r["groups"] for r in results] == [30] * 3
        assert calls == 90
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)

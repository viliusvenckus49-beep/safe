import asyncio

import pytest
from sqlalchemy import func, select

from app.models import AuditEvent, ReputationAdjustment, ReputationEvent, ReputationRequest
from app.services import DomainError, Service

pytestmark = pytest.mark.asyncio
REASON = "Administratoriaus sprendimas"


async def test_pending_requires_approval_and_decision_is_final(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        result = await svc.vote(1, "2", -1, None, "pending", comment="Test comment")
        request = result["reputation_request"]
        assert request.reference.startswith("RP-") and result["score"] == 0
        assert (await svc.pending_reputation(900))[1] == 1
        await svc.moderate_reputation(900, request.reference, True)
        await svc.moderate_reputation(900, request.reference, True)
        assert (await svc.profile("2"))["score"] == -1
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == 1
        with pytest.raises(DomainError, match="already_moderated"):
            await svc.moderate_reputation(900, request.reference, False)


async def test_rejection_does_not_change_score(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        result = await svc.vote(1, "2", 1, None, "reject", comment="Test comment")
        reference = result["reputation_request"].reference
        await svc.moderate_reputation(900, reference, False)
        await svc.moderate_reputation(900, reference, False)
        assert (await svc.profile("2"))["positive"] == 0
        assert (await svc.pending_reputation(900))[1] == 0


async def test_adjust_reset_preserves_history_and_retry(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        target = await svc.resolve("2")
        giver = await svc.resolve("1")
        session.add(
            ReputationEvent(
                giver_user_id=giver.id, receiver_user_id=target.id, value=1, request_key="legacy"
            )
        )
        await session.commit()
        await svc.admin_adjust_rep(900, "2", 12, REASON, "plus")
        await svc.admin_adjust_rep(900, "2", -3, REASON, "minus")
        assert (await svc.profile("2"))["score"] == 10
        profile = await svc.admin_reset_rep(900, "2", REASON, "reset")
        assert (profile["score"], profile["positive"], profile["negative"]) == (0, 0, 0)
        await svc.admin_adjust_rep(900, "2", 4, REASON, "later")
        assert (await svc.admin_reset_rep(900, "2", REASON, "reset"))["score"] == 4
        assert await session.scalar(select(func.count()).select_from(ReputationEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(ReputationAdjustment)) == 4
        with pytest.raises(DomainError, match="invalid_input"):
            await svc.admin_adjust_rep(900, "2", 5, REASON, "later")


async def test_top_visibility_negative_filter_and_order(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        await svc.admin_adjust_rep(900, "1", 20, REASON, "a")
        await svc.admin_adjust_rep(900, "2", 10, REASON, "b")
        await svc.admin_adjust_rep(900, "3", -2, REASON, "c")
        assert await svc.leaderboard() == []
        await svc.set_top_visibility(900, "1", True, REASON, "showfirst")
        await svc.set_top_visibility(900, "2", True, REASON, "showsecond")
        await svc.set_top_visibility(900, "3", True, REASON, "shownegative")
        await svc.set_top_visibility(900, "4", True, REASON, "showzero")
        assert [p["user"].telegram_id for p in await svc.leaderboard()] == [1, 2, 4]
        await svc.set_top_visibility(900, "1", False, REASON, "hide")
        await svc.set_top_visibility(900, "1", False, REASON, "hide")
        assert [p["user"].telegram_id for p in await svc.leaderboard()] == [2, 4]


@pytest.mark.parametrize("operation", ["adjust", "reset", "top", "pending", "moderate"])
async def test_all_admin_operations_deny_nonadmin(database, settings, operation):
    async with database() as session:
        svc = Service(settings, session)
        calls = {
            "adjust": lambda: svc.admin_adjust_rep(1, "2", 1, REASON, "key"),
            "reset": lambda: svc.admin_reset_rep(1, "2", REASON, "key"),
            "top": lambda: svc.set_top_visibility(1, "2", True, REASON, "key"),
            "pending": lambda: svc.pending_reputation(1),
            "moderate": lambda: svc.moderate_reputation(1, "RP-2026-000001", True),
        }
        with pytest.raises(DomainError, match="forbidden"):
            await calls[operation]()


@pytest.mark.parametrize("value", [0, 10001, -10001, True, "1"])
async def test_adjust_validates_bounded_integer(database, settings, value):
    async with database() as session:
        with pytest.raises(DomainError, match="invalid_input"):
            await Service(settings, session).admin_adjust_rep(900, "2", value, REASON, "key")


async def test_concurrent_approval_and_adjustments(database, settings):
    async with database() as session:
        result = await Service(settings, session).vote(
            1, "2", 1, None, "vote", comment="Test comment"
        )
        reference = result["reputation_request"].reference

    async def approve():
        async with database() as session:
            await Service(settings, session).moderate_reputation(900, reference, True)

    async def adjust(i):
        async with database() as session:
            await Service(settings, session).admin_adjust_rep(900, "2", 3, REASON, f"adjust{i}")

    await asyncio.gather(*(approve() for _ in range(4)), *(adjust(i) for i in range(4)))
    async with database() as session:
        assert (await Service(settings, session).profile("2"))["score"] == 13
        assert await session.scalar(select(func.count()).select_from(ReputationRequest)) == 1


async def test_global_reset_history_pending_and_idempotency(database, settings):
    settings.rep_cooldown_seconds = 0
    async with database() as session:
        svc = Service(settings, session)
        await svc.admin_adjust_rep(900, "2", 3, REASON, "global-plus")
        await svc.admin_adjust_rep(900, "2", -3, REASON, "global-minus")
        await svc.admin_adjust_rep(900, "3", -5, REASON, "global-negative")
        pending = await svc.vote(1, "2", 1, None, "global-pending", comment="Test comment")
        reference = pending["reputation_request"].reference
        summary = await svc.admin_reset_all_reputation(900, REASON, "global-reset")
        assert summary == {"users_reset": 2, "requests_rejected": 1}
        for target in ("2", "3"):
            profile = await svc.profile(target)
            assert (profile["score"], profile["positive"], profile["negative"]) == (0, 0, 0)
        assert (await svc.pending_reputation(900))[1] == 0
        with pytest.raises(DomainError, match="already_moderated"):
            await svc.moderate_reputation(900, reference, True)
        newer = await svc.vote(4, "2", 1, None, "after-reset", comment="Test comment")
        await svc.moderate_reputation(900, newer["reputation_request"].reference, True)
        assert await svc.admin_reset_all_reputation(900, REASON, "global-reset") == summary
        assert (await svc.profile("2"))["score"] == 1
        assert await session.scalar(select(func.count()).select_from(ReputationAdjustment)) == 5
        with pytest.raises(DomainError, match="forbidden"):
            await svc.admin_reset_all_reputation(1, REASON, "forbidden")
        with pytest.raises(DomainError, match="invalid_input"):
            await svc.admin_reset_all_reputation(
                900, "Kitoks administratoriaus sprendimas", "global-reset"
            )


async def test_explicit_top_approval_scam_exclusion_and_trusted(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        await svc.observe(42, "example", "Example")
        pending = await svc.vote(1, "42", 1, None, "positive", comment="Test comment")
        await svc.moderate_reputation(900, pending["reputation_request"].reference, True)
        assert await svc.leaderboard() == []
        assert not (await svc.profile("42"))["trusted"]
        await svc.set_top_visibility(900, "42", True, REASON, "explicit-include")
        assert (await svc.profile("42"))["trusted_source"] == "top"
        await svc.add_scam(900, "42")
        assert await svc.leaderboard() == []
        assert not (await svc.profile("42"))["trusted"]
        await svc.remove_scam(900, "42", REASON)
        assert len(await svc.leaderboard()) == 1
        await svc.set_top_visibility(900, "42", False, REASON, "explicit-remove")
        assert await svc.leaderboard() == []
        assert not (await svc.profile("42"))["trusted"]

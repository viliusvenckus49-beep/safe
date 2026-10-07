import re

import pytest
from sqlalchemy import func, select

from app.models import (
    AuditEvent,
    ModerationAction,
    ReputationEvent,
    ReputationRequest,
    ScamRecord,
    UsernameHistory,
)
from app.services import DomainError, Service

pytestmark = pytest.mark.asyncio


async def count(session, model):
    return await session.scalar(select(func.count()).select_from(model))


async def test_rep_events_reconstruct_stats_and_retry(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        first = await svc.vote(1, "100", 1, -10, "v1", comment="Test comment")
        second = await svc.vote(2, "100", -1, -10, "v2", comment="Test comment")
        assert (await svc.profile("100"))["positive"] == 0
        await svc.moderate_reputation(900, first["reputation_request"].reference, True)
        await svc.moderate_reputation(900, second["reputation_request"].reference, True)
        await svc.vote(1, "100", 1, -10, "v1", comment="Test comment")
        profile = await svc.profile("100")
        assert (profile["score"], profile["positive"], profile["negative"]) == (0, 1, 1)
        assert await count(session, ReputationRequest) == 2
        assert await count(session, ReputationEvent) == 0
        assert await svc.leaderboard() == []
        await svc.set_top_visibility(900, "100", True, "Approved trusted TOP member", "include")
        assert (await svc.leaderboard())[0]["user"].telegram_id == 100


@pytest.mark.parametrize(
    "actor,target,value,key,code",
    [
        (1, "1", 1, "self", "self_rep"),
        (1, "2", 0, "bad", "invalid_input"),
        (1, "oops", 1, "bad", "invalid_target"),
    ],
)
async def test_invalid_votes(database, settings, actor, target, value, key, code):
    async with database() as session:
        with pytest.raises(DomainError, match=code):
            await Service(settings, session).vote(
                actor, target, value, None, key, comment="Test comment"
            )
        assert await count(session, ReputationEvent) == 0


async def test_rep_abuse_rules(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        await svc.vote(1, "2", 1, None, "one", comment="Test comment")
        for args, code in [
            ((1, "2", -1, None, "two"), "duplicate_rep"),
            ((1, "3", 1, None, "three"), "rep_cooldown"),
            ((2, "1", 1, None, "four"), "reciprocal_rep"),
            ((3, "2", 1, None, "one"), "invalid_input"),
        ]:
            with pytest.raises(DomainError, match=code):
                await svc.vote(*args, comment="Test comment")
        assert await count(session, ReputationRequest) == 1


async def test_numeric_identity_survives_username_changes_and_reassignment(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        await svc.observe(10, "First_Name", "Alice")
        request = await svc.vote(20, "10", 1, None, "vote", comment="Test comment")
        await svc.moderate_reputation(900, request["reputation_request"].reference, True)
        await svc.add_scam(900, "10", "Confirmed evidence")
        await svc.observe(10, "second_name", "Alice")
        profile = await svc.profile("@SECOND_NAME")
        assert profile["user"].telegram_id == 10 and profile["score"] == 1 and profile["scam"]
        assert await count(session, UsernameHistory) == 2
        await svc.observe(30, "second_name", "Other")
        assert (await svc.resolve("@second_name")).telegram_id == 30
        assert (await svc.profile("10"))["scam"]
        await svc.observe(10, None, "Deleted Account")
        assert (await svc.profile("10"))["user"].username is None


async def test_unknown_username_is_not_merged_into_verified_id(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        unknown = await svc.resolve("@some_name")
        await svc.add_scam(900, f"u:{unknown.id}", "Unverified username record")
        known = await svc.observe(10, "some_name", "Known")
        assert (await svc.resolve("@some_name")).id == known.id
        assert (await svc.profile("10"))["scam"] is None


@pytest.mark.parametrize(
    "target", ["", "@abc", "@<script>", "0", "9223372036854775808", "u:no", "u:999"]
)
async def test_malformed_target(database, settings, target):
    async with database() as session:
        with pytest.raises(DomainError):
            await Service(settings, session).resolve(target)


async def test_report_evidence_idempotency_and_not_auto_scam(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        files = [
            {"kind": "photo", "file_id": "photo"},
            {"kind": "document", "file_id": "doc", "caption": "Evidence"},
        ]
        report = await svc.submit_report(1, "2", "Lost payment evidence", files, "draft")
        assert re.fullmatch(r"SC-\d{4}-\d{6,}", report.reference)
        assert report.status == "PENDING"
        assert (await svc.profile("2"))["scam"] is None
        repeat = await svc.submit_report(1, "2", "Lost payment evidence", files, "draft")
        assert repeat.id == report.id
        details = await svc.report_details(900, report.reference)
        assert len(details["evidence"]) == 2
        with pytest.raises(DomainError, match="report_cooldown"):
            await svc.submit_report(1, "3", "Another payment issue", [], "other")
        with pytest.raises(DomainError, match="invalid_input"):
            await svc.submit_report(4, "2", "Another payment issue", [], "draft")


@pytest.mark.parametrize(
    "reason,evidence,code",
    [
        ("bad", [], "invalid_reason"),
        ("x" * 2001, [], "invalid_reason"),
        ("Valid reason", [{"kind": "exe", "file_id": "x"}], "invalid_evidence"),
        ("Valid reason", [{"kind": "photo", "file_id": ""}], "invalid_evidence"),
        ("Valid reason", [{"kind": "photo", "file_id": "x"}] * 11, "invalid_input"),
    ],
)
async def test_invalid_report(database, settings, reason, evidence, code):
    async with database() as session:
        with pytest.raises(DomainError, match=code):
            await Service(settings, session).submit_report(1, "2", reason, evidence, "key")


async def test_report_self_and_duplicate_pending(database, settings):
    settings.report_cooldown_seconds = 0
    async with database() as session:
        svc = Service(settings, session)
        with pytest.raises(DomainError, match="self_report"):
            await svc.submit_report(1, "1", "Valid reason", [], "self")
        await svc.submit_report(1, "2", "Valid reason", [], "one")
        with pytest.raises(DomainError, match="duplicate_report"):
            await svc.submit_report(1, "2", "Valid reason", [], "two")


@pytest.mark.parametrize("approve", [True, False])
async def test_moderation_idempotency_and_conflicting_decision(database, settings, approve):
    async with database() as session:
        svc = Service(settings, session)
        report = await svc.submit_report(1, "2", "Valid reason", [], "draft")
        await svc.moderate(900, report.reference, approve, "Reviewed")
        await svc.moderate(900, report.reference, approve, "Reviewed")
        assert await count(session, ModerationAction) == 1
        assert await count(session, ScamRecord) == int(approve)
        assert await count(session, AuditEvent) == 1
        with pytest.raises(DomainError, match="already_moderated"):
            await svc.moderate(900, report.reference, not approve)


async def test_admin_permissions_all_paths(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        for method, args in [
            ("add_scam", ("2", "Valid reason")),
            ("remove_scam", ("2", "Valid reason")),
            ("moderate", ("SC-2026-000001", True)),
            ("pending", (0,)),
            ("report_details", ("SC-2026-000001",)),
            ("admin_stats", ()),
            ("audits", (0,)),
        ]:
            with pytest.raises(DomainError, match="forbidden"):
                await getattr(svc, method)(1, *args)
        assert await count(session, AuditEvent) == 0


async def test_scam_history_and_pagination(database, settings):
    async with database() as session:
        svc = Service(settings, session)
        for target in range(1, 8):
            await svc.add_scam(900, str(target), "Confirmed reason")
        with pytest.raises(DomainError, match="duplicate_scam"):
            await svc.add_scam(900, "1", "Another reason")
        assert len((await svc.scams(0))[0]) == 5
        assert len((await svc.scams(1))[0]) == 2
        assert (await svc.scams(0))[1] == 7
        assert await svc.remove_scam(900, "1", "Appeal verified")
        assert not await svc.remove_scam(900, "1", "Repeated removal")
        assert (await svc.profile("1"))["scam"] is None
        await svc.add_scam(900, "1", "New confirmed reason")
        assert await count(session, ScamRecord) == 8
        assert await count(session, AuditEvent) == 9
        with pytest.raises(DomainError, match="invalid_input"):
            await svc.scams(-1)


async def test_metadata_observation_refreshes_cached_user_between_sessions(database, settings):
    async with database() as first, database() as second:
        a, b = Service(settings, first), Service(settings, second)
        old = await a.observe(10, "first_name", "First")
        await b.observe(10, "second_name", "Second")
        restored = await a.observe(10, "first_name", "First again")
        assert restored.id == old.id
        assert restored.username == "first_name"
    async with database() as session:
        user = await Service(settings, session).resolve("10")
        assert user.username == "first_name"
        assert user.display_name == "First again"


@pytest.mark.parametrize("target", ["9" * 10000, "@@valid_name", "u:" + "9" * 10000])
async def test_oversized_or_ambiguous_identifiers_are_domain_errors(database, settings, target):
    async with database() as session:
        with pytest.raises(DomainError, match="invalid_target"):
            await Service(settings, session).resolve(target)

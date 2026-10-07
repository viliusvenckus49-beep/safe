import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Base, User

spec = importlib.util.spec_from_file_location(
    "merge_database", Path(__file__).parents[1] / "deploy/merge_database.py"
)
merge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(merge)


def empty():
    return {"version": 1, "tables": {name: [] for name in Base.metadata.tables}}


def user(id, tg=None, username=None, day=3, language=None):
    return dict(
        id=id,
        telegram_id=tg,
        username=username,
        language=language,
        display_name="Name",
        created_at=f"2026-10-{day:02d}T12:00:00+00:00",
        updated_at=f"2026-10-{day:02d}T12:00:00+00:00",
    )


def test_user_ids_remap_and_shared_telegram_identity_keeps_current_username_and_language():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, 100, "oldname"), user(2, 200, "another")]
    new["tables"]["users"] = [user(1, 200, "newname", 7, "en")]
    old["tables"]["trusted_designations"] = [
        dict(target_id=1, active=True, actor_id=100, updated_at="2026-10-03T12:00:00+00:00")
    ]
    p = merge.merge_plan(old, new, 100)
    assert p["inserts"]["users"][0]["telegram_id"] == 100
    assert p["inserts"]["users"][0]["id"] == 2
    assert p["inserts"]["trusted_designations"][0]["target_id"] == 2
    assert not p["updates"]["users"]
    assert new["tables"]["users"][0]["language"] == "en"


def test_username_reuse_does_not_merge_numeric_identities_or_transfer_trust():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, 100, "reused")]
    new["tables"]["users"] = [user(1, 200, "reused", 7)]
    old["tables"]["trusted_designations"] = [
        dict(target_id=1, active=True, actor_id=100, updated_at="2026-10-03T12:00:00+00:00")
    ]
    p = merge.merge_plan(old, new, 100)
    assert p["inserts"]["users"][0]["username"] is None
    assert p["inserts"]["users"][0]["telegram_id"] == 100
    assert p["inserts"]["trusted_designations"][0]["target_id"] == 2


def test_unknown_username_never_binds_to_known_numeric_identity():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, None, "reused")]
    new["tables"]["users"] = [user(1, 200, "reused", 7)]
    p = merge.merge_plan(old, new, 100)
    assert p["inserts"]["users"][0]["telegram_id"] is None
    assert p["inserts"]["users"][0]["id"] == 2


def report(id, reporter, target, key, ref):
    return dict(
        id=id,
        reference=ref,
        reporter_id=reporter,
        target_id=target,
        reason="Evidence",
        status="REJECTED",
        request_key=key,
        created_at="2026-10-03T12:00:00+00:00",
    )


def test_report_reference_collision_preserves_new_report_and_remaps_evidence_and_audit():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, 100), user(2, 200)]
    new["tables"]["users"] = [user(1, 100), user(2, 200)]
    old["tables"]["reports"] = [report(1, 1, 2, "old", "SC-2026-000001")]
    new["tables"]["reports"] = [report(1, 1, 2, "new", "SC-2026-000001")]
    old["tables"]["report_evidence"] = [
        dict(id=1, report_id=1, kind="photo", file_id="private-evidence", caption=None)
    ]
    old["tables"]["audit_events"] = [
        dict(
            id=1,
            actor_id=100,
            action="report_rejected",
            target_id=2,
            details={"reference": "SC-2026-000001"},
            created_at="2026-10-03T12:00:00+00:00",
        )
    ]
    p = merge.merge_plan(old, new, 100)
    assert p["inserts"]["reports"][0]["reference"] == "SC-2026-000002"
    assert p["inserts"]["report_evidence"][0]["report_id"] == 2
    assert p["inserts"]["audit_events"][0]["details"]["reference"] == "SC-2026-000002"
    assert new["tables"]["reports"][0]["request_key"] == "new"


def test_import_is_idempotent_and_plan_does_not_modify_input():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, 100)]
    original = deepcopy(old)
    p = merge.merge_plan(old, new, 100)
    assert old == original
    new["tables"]["audit_events"] = p["inserts"]["audit_events"]
    assert merge.merge_plan(old, new, 100)["already_imported"]


def test_approved_reputation_collision_aborts_instead_of_inventing_a_decision():
    old, new = empty(), empty()
    old["tables"]["users"] = new["tables"]["users"] = [user(1, 100), user(2, 200)]
    r = dict(
        id=1,
        reference="RP-2026-000001",
        giver_user_id=1,
        receiver_user_id=2,
        value=1,
        chat_id=None,
        request_key="old",
        status="APPROVED",
        moderator_id=100,
        moderated_at="2026-10-03T12:00:00+00:00",
        notes="",
        comment="Useful review",
        created_at="2026-10-03T12:00:00+00:00",
    )
    old["tables"]["reputation_requests"] = [r]
    new["tables"]["reputation_requests"] = [
        dict(r, request_key="new", status="PENDING", moderator_id=None, moderated_at=None)
    ]
    with pytest.raises(merge.MergeConflict, match="approved_reputation_collision"):
        merge.merge_plan(old, new, 100)


def test_newer_admin_revocation_and_consent_are_retained():
    old, new = empty(), empty()
    old["tables"]["administrators"] = [
        dict(telegram_id=100, active=True, assigned_by=900, updated_at="2026-10-03T12:00:00+00:00")
    ]
    new["tables"]["administrators"] = [
        dict(telegram_id=100, active=False, assigned_by=900, updated_at="2026-10-07T12:00:00+00:00")
    ]
    p = merge.merge_plan(old, new, 900)
    assert not p["updates"]["administrators"]


def test_newer_source_profile_keeps_current_ui_language():
    old, new = empty(), empty()
    old["tables"]["users"] = [user(1, 100, "newest", 7, "lt")]
    new["tables"]["users"] = [user(1, 100, "previous", 3, "ru")]
    p = merge.merge_plan(old, new, 100)
    assert p["updates"]["users"][0]["username"] == "newest"
    assert p["updates"]["users"][0]["language"] == "ru"


def test_missing_parent_aborts():
    old, new = empty(), empty()
    old["tables"]["trusted_designations"] = [
        dict(target_id=123, active=True, actor_id=900, updated_at="2026-10-03T12:00:00+00:00")
    ]
    with pytest.raises(merge.MergeConflict, match="missing_parent"):
        merge.merge_plan(old, new, 900)


@pytest.mark.asyncio
async def test_transactional_apply_constraints_and_next_user_allocation(database):
    old = empty()
    old["tables"]["users"] = [user(99, 100, "history")]
    old["tables"]["trusted_designations"] = [
        dict(target_id=99, active=True, actor_id=900, updated_at="2026-10-03T12:00:00+00:00")
    ]
    async with database() as session:
        async with session.begin():
            connection = await session.connection()
            p = merge.merge_plan(old, await merge.snapshot(connection), 900)
            await merge.apply(connection, p)
        session.add(User(telegram_id=200, display_name="New"))
        await session.commit()
        assert len(list(await session.scalars(select(User)))) == 2
        current = await merge.snapshot(await session.connection())
        assert merge.merge_plan(old, current, 900)["already_imported"]
        assert current["tables"]["trusted_designations"][0]["target_id"] == 1


def test_timestamps_compare_in_utc():
    assert merge.moment({"updated_at": "2026-10-07T10:00:00+03:00"}) < merge.moment(
        {"updated_at": "2026-10-07T08:00:00+00:00"}
    )


@pytest.mark.asyncio
async def test_constraint_failure_rolls_back_all_imported_rows(database):
    from sqlalchemy.exc import IntegrityError

    old = empty()
    old["tables"]["users"] = [user(1, 100)]
    old["tables"]["reputation_events"] = [
        dict(
            id=1,
            giver_user_id=1,
            receiver_user_id=1,
            value=1,
            chat_id=None,
            request_key="self",
            created_at="2026-10-03T12:00:00+00:00",
        )
    ]
    async with database() as session:
        with pytest.raises(IntegrityError):
            async with session.begin():
                connection = await session.connection()
                await merge.apply(
                    connection, merge.merge_plan(old, await merge.snapshot(connection), 900)
                )
        assert not list(await session.scalars(select(User)))

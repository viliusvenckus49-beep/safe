import sqlite3

import pytest
from sqlalchemy import func, select

from app.db import Database
from app.import_v1 import ImportFailure, import_database
from app.models import Base, ModerationAction, Report, ReputationEvent, ScamRecord, User


def source_file(tmp_path, bad=False):
    path = tmp_path / "v1.db"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE users(id INTEGER PRIMARY KEY, tg_id INTEGER, username TEXT, created_at TEXT);
        CREATE TABLE reputation(id INTEGER PRIMARY KEY, giver_tg_id INTEGER, target_user_id INTEGER, value INTEGER, created_at TEXT);
        CREATE TABLE scam_entries(id INTEGER PRIMARY KEY, user_id INTEGER, reason TEXT, added_by INTEGER, active INTEGER, created_at TEXT);
        CREATE TABLE reports(id INTEGER PRIMARY KEY, reporter_tg_id INTEGER, target_user_id INTEGER, reason TEXT, status TEXT, created_at TEXT);
        INSERT INTO users VALUES(1, 123, 'known_user', '2026-01-01T00:00:00');
        INSERT INTO users VALUES(2, NULL, 'known_user', '2026-01-01T00:00:00');
        INSERT INTO reputation VALUES(1, 456, 2, 1, '2026-01-01T00:00:00');
        INSERT INTO scam_entries VALUES(1, 2, 'reason', 900, 0, '2026-01-01T00:00:00');
        INSERT INTO reports VALUES(1, 456, 2, 'reason', 'approved', '2026-01-01T00:00:00');
        INSERT INTO reports VALUES(2, 456, 1, 'reason', 'rejected', '2026-01-01T00:00:00');
        INSERT INTO reports VALUES(3, 456, 1, 'reason', 'pending', '2026-01-01T00:00:00');
    """)
    if bad:
        connection.execute("UPDATE reports SET status='invalid' WHERE id=3")
    connection.commit()
    connection.close()
    return path


@pytest.mark.asyncio
async def test_import_preserves_distinct_identities_and_statuses(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/new.db")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        path = source_file(tmp_path)
        counts = await import_database(path, database)
        assert counts["reports"] == 3
        async with database.sessions() as session:
            users = list(
                (await session.scalars(select(User).where(User.username == "known_user"))).all()
            )
            assert len(users) == 2
            assert {u.telegram_id for u in users} == {None, 123}
            assert (await session.scalar(select(ReputationEvent))).receiver_user_id == next(
                u.id for u in users if u.telegram_id is None
            )
            reports = list((await session.scalars(select(Report).order_by(Report.id))).all())
            assert [r.status for r in reports] == ["APPROVED", "REJECTED", "PENDING"]
            assert reports[0].reference == "SC-2026-000001"
            assert await session.scalar(select(func.count()).select_from(ModerationAction)) == 2
            scam = await session.scalar(select(ScamRecord))
            assert scam.status == "REMOVED"
            assert scam.removed_at is None
        with pytest.raises(ImportFailure, match="empty"):
            await import_database(path, database)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_import_invalid_status_rolls_back_everything(tmp_path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/new.db")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        with pytest.raises(ImportFailure, match="status"):
            await import_database(source_file(tmp_path, bad=True), database)
        async with database.sessions() as session:
            for table in Base.metadata.sorted_tables:
                assert await session.scalar(select(func.count()).select_from(table)) == 0
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_import_rejects_source_as_destination(tmp_path):
    path = source_file(tmp_path)
    database = Database(f"sqlite+aiosqlite:///{path}")
    try:
        with pytest.raises(ImportFailure, match="different"):
            await import_database(path, database)
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_import_gapped_report_ids_do_not_collide_with_new_reports(tmp_path, settings):
    from app.services import Service

    path = source_file(tmp_path)
    with sqlite3.connect(path) as source:
        source.execute("DELETE FROM reports WHERE id != 2")
    database = Database(f"sqlite+aiosqlite:///{tmp_path}/new.db")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await import_database(path, database)
        async with database.sessions() as session:
            imported = await session.scalar(select(Report))
            assert imported.reference == f"SC-{imported.created_at.year}-{imported.id:06d}"
            new = await Service(settings, session).submit_report(
                789, "123", "Pakankamai ilga priežastis", [], "new-after-import"
            )
            assert new.id != imported.id
            assert new.reference != imported.reference
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_import_accepts_migration_owned_identity_lock(tmp_path):
    from app.models import IdentityLock

    database = Database(f"sqlite+aiosqlite:///{tmp_path}/new.db")
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session:
            session.add(IdentityLock(id=1))
            await session.commit()
        assert (await import_database(source_file(tmp_path), database))["reports"] == 3
        async with database.sessions() as session:
            assert await session.get(IdentityLock, 1)
    finally:
        await database.close()

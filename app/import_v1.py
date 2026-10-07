"""Explicit, atomic import of a V1 SQLite database into a migrated empty database."""

import argparse
import asyncio
import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db import Database
from app.models import (
    AuditEvent,
    Base,
    ModerationAction,
    OperationLock,
    Report,
    ReputationEvent,
    ScamRecord,
    User,
    UsernameHistory,
)


class ImportFailure(ValueError):
    """Safe public import failure; never include source rows or connection URLs."""


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


def read_source(source: Path) -> dict[str, list[dict]]:
    if not source.is_file():
        raise ImportFailure("Source SQLite file does not exist.")
    connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("users", "reputation", "scam_entries", "reports")
        }
    except sqlite3.Error:
        raise ImportFailure("Source is not a supported V1 SQLite database.") from None
    finally:
        connection.close()


async def import_database(source: Path, database: Database) -> dict[str, int]:
    destination = make_url(str(database.engine.url))
    if (
        destination.drivername.startswith("sqlite")
        and destination.database is not None
        and destination.database
        not in (
            None,
            ":memory:",
        )
    ):
        if Path(destination.database).resolve() == source.resolve():
            raise ImportFailure("Source and destination must be different databases.")
    data = read_source(source)
    fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]
    counts = {name: len(rows) for name, rows in data.items()}
    async with database.sessions() as session, session.begin():
        # Every application table must be empty, including audit and lock tables.
        for table in Base.metadata.sorted_tables:
            if table.name == "identity_lock":
                continue  # Migration-owned coordination row is not imported user data.
            if await session.scalar(select(func.count()).select_from(table)):
                raise ImportFailure("Destination must be empty; import never merges existing data.")
        mapping: dict[int, User] = {}
        identities: dict[int, User] = {}
        for row in data["users"]:
            username = row["username"].lstrip("@").lower() if row["username"] else None
            if username and not re.fullmatch(r"[a-z0-9_]{5,32}", username):
                raise ImportFailure("Source contains an invalid username.")
            tg_id = row["tg_id"]
            if tg_id is not None and (not isinstance(tg_id, int) or tg_id <= 0):
                raise ImportFailure("Source contains an invalid Telegram identity.")
            user = User(
                telegram_id=tg_id, username=username, created_at=timestamp(row["created_at"])
            )
            session.add(user)
            await session.flush()
            mapping[row["id"]] = user
            if tg_id is not None:
                identities[tg_id] = user
            if username:
                session.add(
                    UsernameHistory(user_id=user.id, username=username, observed_at=user.created_at)
                )
            session.add(OperationLock(user_id=user.id))

        async def identity(tg_id: int) -> User:
            if not isinstance(tg_id, int) or tg_id <= 0:
                raise ImportFailure("Source contains an invalid Telegram identity.")
            if tg_id not in identities:
                user = User(telegram_id=tg_id)
                session.add(user)
                await session.flush()
                session.add(OperationLock(user_id=user.id))
                identities[tg_id] = user
            return identities[tg_id]

        for row in data["reputation"]:
            giver = await identity(row["giver_tg_id"])
            session.add(
                ReputationEvent(
                    giver_user_id=giver.id,
                    receiver_user_id=mapping[row["target_user_id"]].id,
                    value=row["value"],
                    created_at=timestamp(row["created_at"]),
                    request_key=f"v1:{fingerprint}:rep:{row['id']}",
                )
            )
        for row in data["scam_entries"]:
            target = mapping[row["user_id"]]
            active = bool(row["active"])
            session.add(
                ScamRecord(
                    target_id=target.id,
                    username_snapshot=target.username,
                    reason=row["reason"],
                    status="ACTIVE" if active else "REMOVED",
                    moderator_id=row["added_by"],
                    created_at=timestamp(row["created_at"]),
                    removal_reason=None
                    if active
                    else "V1 importas: pašalinimo data ir moderatorius nežinomi.",
                )
            )
        for row in data["reports"]:
            status = row["status"].upper()
            if status not in ("PENDING", "APPROVED", "REJECTED"):
                raise ImportFailure("Source contains an unsupported report status.")
            reporter = await identity(row["reporter_tg_id"])
            created = timestamp(row["created_at"])
            report = Report(
                reference=f"V1-TMP-{row['id']}",
                reporter_id=reporter.id,
                target_id=mapping[row["target_user_id"]].id,
                reason=row["reason"],
                status=status,
                created_at=created,
                request_key=f"v1:{fingerprint}:report:{row['id']}",
            )
            session.add(report)
            await session.flush()
            report.reference = f"SC-{created.year}-{report.id:06d}"
            if status != "PENDING":
                session.add(
                    ModerationAction(
                        report_id=report.id,
                        moderator_id=0,
                        decision=status,
                        notes="V1 importas: sprendimo data ir moderatorius nežinomi.",
                        created_at=created,
                    )
                )
        session.add(
            AuditEvent(
                actor_id=0,
                action="V1_IMPORT",
                details={
                    "fingerprint": fingerprint,
                    "counts": counts,
                    "history_limitations": "V1 stored mutable votes and no removal/moderation attribution.",
                },
            )
        )
        await session.flush()
    return counts


async def run(source: Path) -> None:
    database = Database(Settings().database_url)
    try:
        counts = await import_database(source, database)
        print(json.dumps(counts, sort_keys=True))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.source))
    except Exception:
        # SQL/Pydantic exceptions can contain credentials or private source data.
        parser.exit(
            1,
            "Import failed. Check configuration, migrated empty destination and valid V1 source. No changes committed.\n",
        )


if __name__ == "__main__":
    main()

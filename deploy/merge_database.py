#!/usr/bin/env python3
"""Transactional, idempotent merge of two SAFECheck snapshots; never print record contents."""

import argparse
import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, insert, select, text, update

from app.db import create_database
from app.models import Base


class MergeConflict(Exception):
    pass


def normalized(value):
    if value is None:
        return None
    return str(value).casefold().lstrip("@")


def moment(row):
    values = []
    for key in ("moderated_at", "updated_at", "removed_at", "created_at", "observed_at"):
        if row.get(key):
            value = datetime.fromisoformat(str(row[key]))
            values.append((value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC))
    return max(values).isoformat() if values else ""


def logical_key(name, row):
    if name == "users":
        if row.get("telegram_id") is not None:
            return ("telegram", row["telegram_id"])
        return ("unknown", normalized(row["username"])) if row.get("username") else None
    if name in {"reputation_events", "reputation_requests"}:
        return (row["giver_user_id"], row["receiver_user_id"])
    if name == "username_history":
        return (row["user_id"], row["username"], row["observed_at"])
    if name == "scam_records":
        return (row["target_id"], row["created_at"], row["moderator_id"])
    if name == "report_evidence":
        return (row["report_id"], row["kind"], row["file_id"], row["caption"])
    if name == "moderation_actions":
        return (row["report_id"],)
    if name == "ban_actions":
        return (row["chat_id"], row["telegram_id"], row["scam_record_id"])
    if name == "recovery_deliveries":
        return (row["campaign_id"], row["telegram_id"])
    if "request_key" in row:
        return (row["request_key"],)
    table = Base.metadata.tables[name]
    pk = list(table.primary_key.columns)
    if len(pk) != 1 or pk[0].name != "id" or name == "identity_lock":
        return tuple(row[c.name] for c in pk)
    return None


def validate_snapshot(value):
    tables = Base.metadata.tables
    if value.get("version") != 1 or set(value.get("tables", {})) != set(tables):
        raise MergeConflict("snapshot_schema")
    for name, rows in value["tables"].items():
        expected = {c.name for c in tables[name].columns}
        if any(set(row) != expected for row in rows):
            raise MergeConflict("snapshot_columns")


def payload_digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def merge_plan(source, target, actor):
    validate_snapshot(source)
    validate_snapshot(target)
    digest = payload_digest(source)
    for row in target["tables"]["audit_events"]:
        if (
            row["action"] == "legacy_database_import"
            and row["details"].get("source_digest") == digest
        ):
            return {"already_imported": True, "inserts": {}, "updates": {}, "summary": {}}
    inserts, updates, mappings, summary, references, conflicts = {}, {}, {}, Counter(), {}, []
    for table in Base.metadata.sorted_tables:
        name = table.name
        inserts[name], updates[name], mappings[name] = [], [], {}
        rows = [dict(row) for row in target["tables"][name]]
        keys = {logical_key(name, row): row for row in rows if logical_key(name, row) is not None}
        requests = {row["request_key"]: row for row in rows if "request_key" in row}
        pk_names = [c.name for c in table.primary_key.columns]
        next_id = max((row.get("id", 0) for row in rows), default=0) + 1
        used_refs = {row["reference"] for row in rows if "reference" in row}
        for original in source["tables"][name]:
            row = dict(original)
            old_pk = tuple(original[key] for key in pk_names)
            for column in table.columns:
                if row[column.name] is not None and column.foreign_keys:
                    fk = next(iter(column.foreign_keys))
                    try:
                        row[column.name] = mappings[fk.column.table.name][(row[column.name],)][0]
                    except KeyError as exc:
                        raise MergeConflict("missing_parent:" + name) from exc
            key = logical_key(name, row)
            existing = requests.get(row.get("request_key")) if "request_key" in row else None
            if existing is None and key is not None:
                existing = keys.get(key)
            if existing is not None:
                if "request_key" in row and existing["request_key"] == row["request_key"]:
                    identity_fields = [
                        k
                        for k in (
                            "target_id",
                            "giver_user_id",
                            "receiver_user_id",
                            "reporter_id",
                            "value",
                            "positive_delta",
                            "negative_delta",
                        )
                        if k in row
                    ]
                    if any(row[k] != existing[k] for k in identity_fields):
                        raise MergeConflict("request_collision:" + name)
                if name == "reputation_events" and row["value"] != existing["value"]:
                    raise MergeConflict("reputation_event_collision")
                if name == "reputation_requests" and row["request_key"] != existing["request_key"]:
                    # Do not invent moderation decisions or silently lose approved reputation.
                    if row["status"] == "APPROVED" or existing["status"] == "APPROVED":
                        raise MergeConflict("approved_reputation_collision")
                mappings[name][old_pk] = tuple(existing[k] for k in pk_names)
                if "reference" in original:
                    references[original["reference"]] = existing["reference"]
                if name in {
                    "users",
                    "managed_groups",
                    "observed_members",
                    "recovery_subscriptions",
                    "administrators",
                    "trusted_designations",
                    "reputation_requests",
                    "reports",
                } and moment(row) > moment(existing):
                    changed = dict(row)
                    changed.update({k: existing[k] for k in pk_names})
                    if "reference" in changed:
                        changed["reference"] = existing["reference"]
                    # Preserve an explicitly selected interface language from the current VPS.
                    if name == "users" and existing.get("language"):
                        changed["language"] = existing["language"]
                    updates[name].append(changed)
                    existing.update(changed)
                    summary[name + ":updated"] += 1
                if row != existing:
                    conflicts.append({"table": name, "source_row": original})
                summary[name + ":matched"] += 1
                continue
            if pk_names == ["id"] and name != "identity_lock":
                row["id"] = next_id
                next_id += 1
            if "reference" in row:
                old_ref = row["reference"]
                if old_ref in used_refs:
                    prefix = old_ref.rsplit("-", 1)[0]
                    number = row["id"]
                    while f"{prefix}-{number:06d}" in used_refs:
                        number += 1
                    row["reference"] = f"{prefix}-{number:06d}"
                    summary["references:renamed"] += 1
                used_refs.add(row["reference"])
                references[old_ref] = row["reference"]
            if name == "scam_records" and row["status"] == "ACTIVE":
                if any(
                    r["target_id"] == row["target_id"] and r["status"] == "ACTIVE" for r in rows
                ):
                    raise MergeConflict("active_scam_collision")
            if name in {"ban_actions", "recovery_deliveries"} and row["status"] in {
                "PENDING",
                "PROCESSING",
            }:
                # Historical delivery requests never trigger unsolicited replay after restoration.
                row["status"] = "OBSOLETE"
                row["claimed_at"] = None
                row["result_type"] = "legacy_import_no_replay"
                summary["historical_deliveries:retired"] += 1
            mappings[name][old_pk] = tuple(row[k] for k in pk_names)
            rows.append(row)
            inserts[name].append(row)
            if key is not None:
                keys[key] = row
            if "request_key" in row:
                requests[row["request_key"]] = row
            summary[name + ":inserted"] += 1
    # Resolve references only after all parent IDs and colliding display references are known.
    for row in inserts["audit_events"]:
        row["details"] = dict(row["details"])
        if row["details"].get("reference") in references:
            row["details"]["reference"] = references[row["details"]["reference"]]
        if row["action"] == "scam_identity_supplemented" and row["details"].get("record_id"):
            row["details"]["record_id"] = mappings["scam_records"][(row["details"]["record_id"],)][
                0
            ]
    # A reused username never joins different Telegram identities or hides the current owner.
    users = {row["id"]: dict(row) for row in target["tables"]["users"]}
    for row in updates["users"] + inserts["users"]:
        users[row["id"]] = row
    owners = {}
    for row in users.values():
        if row["telegram_id"] is not None and row["username"]:
            owners.setdefault(normalized(row["username"]), []).append(row)
    inserted_ids = {row["id"] for row in inserts["users"]}
    for duplicates in owners.values():
        winner = max(duplicates, key=lambda row: (moment(row), row["id"] not in inserted_ids))
        for row in duplicates:
            if row is winner:
                continue
            row["username"] = None
            if row["id"] not in inserted_ids:
                updates["users"] = [r for r in updates["users"] if r["id"] != row["id"]]
                updates["users"].append(row)
            summary["usernames:retired"] += 1
    # The complete source remains in private backup. Preserve colliding histories in the audit DB.
    audit_id = (
        max(
            (r["id"] for r in target["tables"]["audit_events"] + inserts["audit_events"]), default=0
        )
        + 1
    )
    inserts["audit_events"].append(
        {
            "id": audit_id,
            "actor_id": actor,
            "action": "legacy_database_import",
            "target_id": None,
            "details": {
                "source_digest": digest,
                "counts": dict(summary),
                "colliding_history": conflicts,
            },
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    return {
        "already_imported": False,
        "inserts": inserts,
        "updates": updates,
        "summary": dict(summary),
    }


def json_value(value):
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).isoformat()
    return value


async def snapshot(connection):
    result = {"version": 1, "tables": {}}
    for table in Base.metadata.sorted_tables:
        rows = (
            await connection.execute(select(table).order_by(*table.primary_key.columns))
        ).mappings()
        result["tables"][table.name] = [{k: json_value(v) for k, v in row.items()} for row in rows]
    return result


def convert_row(table, row):
    return {
        key: datetime.fromisoformat(value)
        if value is not None and isinstance(table.c[key].type, DateTime)
        else value
        for key, value in row.items()
    }


async def apply(connection, plan):
    for table in Base.metadata.sorted_tables:
        name = table.name
        for row in plan["updates"].get(name, []):
            condition = [table.c[c.name] == row[c.name] for c in table.primary_key.columns]
            await connection.execute(
                update(table).where(*condition).values(**convert_row(table, row))
            )
        rows = plan["inserts"].get(name, [])
        if rows:
            await connection.execute(insert(table), [convert_row(table, row) for row in rows])
        if connection.dialect.name == "postgresql" and "id" in table.c and name != "identity_lock":
            await connection.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE(MAX(id),1), MAX(id) IS NOT NULL) FROM {name}"
                )
            )


async def run(args):
    engine, _ = create_database(os.environ["DATABASE_URL"])
    try:
        async with engine.begin() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
                if args.mode != "export":
                    await connection.execute(text("SELECT pg_advisory_xact_lock(7384619)"))
            current = await snapshot(connection)
            target_digest = payload_digest(current)
            if args.expected_target_digest and target_digest != args.expected_target_digest:
                raise MergeConflict("target_changed_since_validation")
            if args.mode == "export":
                args.file.write_text(json.dumps(current, ensure_ascii=False, separators=(",", ":")))
                args.file.chmod(0o600)
                print(json.dumps({name: len(rows) for name, rows in current["tables"].items()}))
                return
            source = json.loads(args.file.read_text())
            plan = merge_plan(source, current, args.actor)
            if args.mode == "apply" and not plan["already_imported"]:
                await apply(connection, plan)
            print(
                json.dumps(
                    {
                        "already_imported": plan["already_imported"],
                        "counts": plan["summary"],
                        "target_digest": target_digest,
                    },
                    sort_keys=True,
                )
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["export", "plan", "apply"])
    parser.add_argument("file", type=Path)
    parser.add_argument("--actor", type=int, default=28563234)
    parser.add_argument("--expected-target-digest")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except MergeConflict as exc:
        raise SystemExit("Merge refused: " + str(exc)) from None
    except Exception:
        raise SystemExit(
            "Data operation failed; records and credentials are not displayed"
        ) from None

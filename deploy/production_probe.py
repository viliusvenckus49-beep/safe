"""Private, read-only PostgreSQL retention evidence. Never output rows or credentials."""

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import asyncpg


def quoted(value):
    return '"' + value.replace('"', '""') + '"'


def fingerprint(rows, columns, *, transform=False, table=""):
    encoded = []
    for row in rows:
        values = json.loads(row["item"])
        if transform and table == "ad_assignment_policies":
            data = dict(zip(columns, values, strict=True))
            if data.get("mode") == "FUTURE":
                data.update(mode="EXISTING", auto_activate=False, reconcile_at=None)
                data["version"] += 1
                values = [data[name] for name in columns]
        encoded.append(
            json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    digest = hashlib.sha256()
    for row in sorted(encoded):
        payload = row.encode()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return {"count": len(encoded), "sha256": digest.hexdigest()}


async def evidence(connection, baseline=None):
    await connection.execute("SET TIME ZONE 'UTC'")
    async with connection.transaction(readonly=True, isolation="repeatable_read"):
        schema = await connection.fetchval("SELECT current_schema()")
        head = await connection.fetchval("SELECT version_num FROM alembic_version")
        if baseline is None:
            tables = {}
            records = await connection.fetch(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name<>'alembic_version' "
                "ORDER BY table_name,ordinal_position",
                schema,
            )
            for record in records:
                tables.setdefault(record["table_name"], {"columns": []})["columns"].append(
                    record["column_name"]
                )
            baseline = {"head": head, "tables": tables}
            if "audit_events" in tables:
                baseline["audit_max"] = await connection.fetchval(
                    "SELECT COALESCE(max(id),0) FROM audit_events"
                )
        result = {"head": head, "tables": {}, "audit_max": baseline.get("audit_max", 0)}
        for table, spec in baseline["tables"].items():
            columns = spec["columns"]
            fields = ",".join(quoted(name) for name in columns)
            where = " WHERE id <= $1" if table == "audit_events" else ""
            args = [baseline.get("audit_max", 0)] if where else []
            rows = await connection.fetch(
                f"SELECT json_build_array({fields})::text AS item FROM {quoted(table)}{where}",
                *args,
            )
            result["tables"][table] = {
                "columns": columns,
                "original": fingerprint(rows, columns),
                "expected": fingerprint(rows, columns, transform=True, table=table),
            }
        invalid = await connection.fetchval(
            "SELECT count(*) FROM pg_constraint WHERE connamespace=$1::regnamespace AND NOT convalidated",
            schema,
        )
        if invalid:
            raise RuntimeError("Unvalidated database constraints")
        return result


def verify(baseline, actual, *, original=False):
    expected_head = baseline["head"] if original else "0023"
    if actual["head"] != expected_head or actual["tables"].keys() != baseline["tables"].keys():
        raise RuntimeError("Database revision or table retention mismatch")
    for table, spec in baseline["tables"].items():
        if spec["original" if original else "expected"] != actual["tables"][table]["original"]:
            raise RuntimeError("Retained rows changed: " + table)


async def main():
    from app.config import Settings

    settings = Settings()
    if sys.argv[1:] == ["identity"]:
        from aiogram import Bot

        from app.bot.session import telegram_session
        from app.mtproto_relay import private_configuration

        if settings.group_help_enabled or settings.moderation_mtproto_enabled:
            private_configuration(Path(settings.group_help_state_dir))

        if settings.bot_token.get_secret_value().split(":", 1)[0] == "8986848376":
            raise RuntimeError("Staging identity refused for production")
        async with Bot(
            settings.bot_token.get_secret_value(), session=telegram_session(settings)
        ) as bot:
            me = await bot.get_me()
        print(
            json.dumps(
                {
                    "bot_id": me.id,
                    "username": me.username,
                    "group_owner_configured": settings.group_owner is not None,
                    "private_relay_enabled": settings.group_help_enabled,
                    "username_lookup_enabled": settings.moderation_mtproto_enabled,
                }
            )
        )
        return
    connection = await asyncpg.connect(
        settings.database_url.replace("postgresql+asyncpg:", "postgresql:")
    )
    try:
        baseline = json.load(sys.stdin) if len(sys.argv) > 1 else None
        result = await evidence(connection, baseline)
        if baseline is not None:
            verify(baseline, result, original=sys.argv[1] == "original")
            print(json.dumps({"head": result["head"], "tables_verified": len(result["tables"])}))
        else:
            print(json.dumps(result, sort_keys=True))
    finally:
        await connection.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("Production retention probe failed", file=sys.stderr)
        raise SystemExit(1) from None

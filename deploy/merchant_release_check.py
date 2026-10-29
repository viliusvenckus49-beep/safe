"""Read-only production diagnostics; never send Telegram messages or change data."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

D = ["docker", "--host=unix:///var/run/docker.sock"]


def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=90)


def emit(key, value):
    print(key + "=" + json.dumps(value, sort_keys=True), flush=True)


def inspect(name):
    return json.loads(run(D + ["inspect", name]))[0]


def sql(query):
    output = run(
        D
        + [
            "exec",
            "safecheck-postgres-1",
            "psql",
            "-X",
            "-U",
            "safecheck",
            "-d",
            "safecheck",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            "BEGIN READ ONLY; " + query + "; COMMIT;",
        ]
    )
    return [line for line in output.splitlines() if line not in ("BEGIN", "COMMIT")]


def main():
    phase, sha = sys.argv[1:]
    assert phase in ("inspect", "verify") and re.fullmatch(r"[0-9a-f]{40}", sha)
    current = inspect("safecheck-bot-1")
    root = Path(current["Config"]["Labels"]["com.docker.compose.project.working_dir"]).parent / sha
    spec = importlib.util.spec_from_file_location(
        "release_diagnostic", root / "deploy/production_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = module.Release(root, sha)
    for cid in run(D + ["ps", "-aq"]).split():
        row = inspect(cid)
        emit(
            "ContainerInventory",
            {
                "name": row["Name"],
                "id": row["Id"],
                "image": row["Config"]["Image"],
                "started": row["State"]["StartedAt"],
                "running": row["State"]["Running"],
                "health": row["State"].get("Health", {}).get("Status"),
            },
        )
    for name in ("safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"):
        row = inspect(name)
        emit(
            "ServiceHealth", {"name": name, "health": row["State"].get("Health", {}).get("Status")}
        )
        assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
    emit("Schema", sql("SELECT version_num FROM alembic_version"))
    for table, condition in (
        ("group_sanction_jobs", module.BLOCKING_NATIVE_JOBS),
        ("group_sanctions", "status IN ('PENDING','REVOKING','UNCERTAIN')"),
        ("ad_publication_attempts", "status IN ('CLAIMED','SENDING','UNCERTAIN')"),
        ("group_check_events", "state IN ('PENDING','WAITING','SENDING','QUEUED')"),
    ):
        emit(
            "BlockingWork",
            {"table": table, "count": sql("SELECT count(*) FROM " + table + " WHERE " + condition)},
        )
    for label, check in (
        ("Idle", release.idle),
        ("Writers", release.writers),
        ("DataServices", release.preserve_services),
    ):
        try:
            check()
            emit("ReleaseGuard", {"check": label, "passed": True})
        except RuntimeError as error:
            # These guard messages are static reviewed strings, never command output.
            emit("ReleaseGuard", {"check": label, "passed": False, "reason": str(error)})
    if phase == "verify":
        row = inspect("safecheck-bot-1")
        assert row["Config"]["Image"].endswith("-" + sha[:12])
        assert sql("SELECT version_num FROM alembic_version") == ["0024"]
        for table in (
            "merchant_entries",
            "merchant_proposals",
            "merchant_search_grams",
            "merchant_settings",
        ):
            emit("CatalogRows", {"table": table, "count": sql("SELECT count(*) FROM " + table)})
        emit("MerchantDeploymentVerified", {"sha": sha, "healthy": True})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("DiagnosticFailed", {"type": type(error).__name__})
        raise SystemExit(1) from None

"""Read-only release guard diagnostics. Never prints config values or raw exception text."""

import json
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    sha = sys.argv[1]
    assert re.fullmatch(r"[a-f0-9]{40}", sha)
    rows = json.loads(
        subprocess.check_output(["docker", "inspect", "safecheck-bot-1"], stderr=subprocess.DEVNULL)
    )
    row = rows[0]
    current = Path(row["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    root = current.parent / sha
    module = runpy.run_path(str(root / "deploy/production_release.py"))
    reasons = {
        "Unfinished or uncertain production work; reconcile first": "UNFINISHED_WORK",
        "Another running container shares the production network": "SHARED_NETWORK_WRITER",
        "Unreviewed migration chain": "MIGRATION_CHAIN",
        "Missing production source": "MISSING_SOURCE",
        "Original production bot is not running": "BOT_NOT_RUNNING",
        "Production data service is not healthy": "DATA_SERVICE_HEALTH",
        "Unknown production Compose project": "COMPOSE_PROJECT",
    }
    try:
        release = module["Release"](root, sha)
    except Exception as e:
        print(
            "ReleaseGuard="
            + json.dumps({"step": "constructor", "reason": reasons.get(str(e), type(e).__name__)}),
            flush=True,
        )
        return
    for name in ("idle", "writers"):
        try:
            getattr(release, name)()
            status = "PASS"
        except Exception as e:
            status = reasons.get(str(e), type(e).__name__)
        print("ReleaseGuard=" + json.dumps({"step": name, "status": status}), flush=True)
    for table, condition in (
        ("group_sanction_jobs", module["BLOCKING_NATIVE_JOBS"]),
        ("group_sanctions", "status IN ('PENDING','REVOKING','UNCERTAIN')"),
        ("ad_publication_attempts", "status IN ('CLAIMED','SENDING','UNCERTAIN')"),
        ("group_check_events", "state IN ('PENDING','WAITING','SENDING','QUEUED')"),
    ):
        lines = release.sql("SELECT count(*) FROM " + table + " WHERE " + condition)
        print(
            "ReleaseGuard="
            + json.dumps(
                {"table": table, "blocking_count": next(int(v) for v in lines if v.isdigit())}
            ),
            flush=True,
        )
    available = next(
        int(line.split()[1]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemAvailable:")
    )
    print(
        "ReleaseGuard="
        + json.dumps(
            {
                "free_bytes": shutil.disk_usage(root).free,
                "available_memory_bytes": available,
                "image": row["Config"]["Image"],
                "bot_health": row["State"].get("Health", {}).get("Status"),
                "readonly": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ReleaseGuard=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

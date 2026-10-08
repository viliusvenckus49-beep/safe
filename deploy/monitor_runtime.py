#!/usr/bin/env python3
"""Follow the active release and retain bounded, aggregate-only monitoring history."""

import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

METRICS = {
    "ban_terminal",
    "recovery_terminal",
    "oldest_pending_seconds",
    "pending_reports",
    "pending_rep",
}


def aggregate(raw: str) -> dict:
    for line in reversed(raw.splitlines()):
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or type(data.get("healthy")) is not bool:
            continue
        metrics = data.get("metrics", {})
        if not isinstance(metrics, dict) or any(
            type(v) is not int or v < 0 for v in metrics.values()
        ):
            continue
        services = data.get("services", {})
        return {
            "healthy": data["healthy"],
            "metrics": {key: metrics[key] for key in METRICS if key in metrics},
            "services": {
                key: services[key]
                for key in ("bot", "postgres", "redis", "outbox", "maintenance")
                if isinstance(services, dict)
                and isinstance(services.get(key), str)
                and services.get(key)
                in {
                    "healthy",
                    "unhealthy",
                    "running",
                    "stopped",
                    "missing",
                    "current",
                    "stale",
                    "unavailable",
                }
            },
        }
    return {
        "healthy": "SAFECheck maintenance in progress" in raw,
        "maintenance": "SAFECheck maintenance in progress" in raw,
    }


def record_history(path: Path, result: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    logger = logging.Logger("safecheck-monitor-history")
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=7, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
        logger.addHandler(handler)
        logger.warning(
            json.dumps({"timestamp": datetime.now(UTC).isoformat(), **result}, sort_keys=True)
        )
    finally:
        handler.close()


def run() -> int:
    os.umask(0o077)
    result, exit_code = {"healthy": False, "monitor": "unavailable"}, 1
    try:
        inspected = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                "safecheck-bot-1",
            ],
            capture_output=True,
            timeout=15,
            check=True,
        )
        root = Path(inspected.stdout.decode().strip())
        if not root.is_absolute() or not (root / "deploy/ops.py").is_file():
            raise ValueError("Active release unavailable")
        monitored = subprocess.run(
            [
                sys.executable,
                str(root / "deploy/ops.py"),
                "--root",
                str(root),
                *sys.argv[1:],
                "monitor",
            ],
            capture_output=True,
            timeout=150,
            check=False,
        )
        result, exit_code = (
            aggregate(monitored.stdout.decode(errors="replace")),
            int(monitored.returncode != 0),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    record_history(Path("/var/lib/safecheck/monitor-history.jsonl"), result)
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())

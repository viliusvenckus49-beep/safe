"""Heartbeat for successful polling and background work, not merely a live PID."""

import json
import math
import os
import sys
import time
from pathlib import Path


class RuntimeHealth:
    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("HEALTH_PATH", "/tmp/safecheck-health.json"))
        self.progress = {"started_at": time.time(), "poll_at": 0.0, "worker_at": 0.0}
        self._write()

    def _write(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(self.progress, file)
        temporary.replace(self.path)

    def polling(self) -> None:
        self.progress["poll_at"] = time.time()
        self._write()

    def worker(self) -> None:
        self.progress["worker_at"] = time.time()
        self._write()

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


def healthy(path: Path, max_age: float = 180, *, current: float | None = None) -> bool:
    try:
        current = time.time() if current is None else current
        if not math.isfinite(max_age) or max_age <= 0:
            return False
        data = json.loads(path.read_text())
        return all(
            isinstance(data.get(key), (int, float))
            and not isinstance(data[key], bool)
            and math.isfinite(data[key])
            and 0 <= current - data[key] <= max_age
            for key in ("poll_at", "worker_at")
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def run() -> None:
    try:
        valid = healthy(
            Path(os.getenv("HEALTH_PATH", "/tmp/safecheck-health.json")),
            float(os.getenv("HEALTH_MAX_AGE_SECONDS", "180")),
        )
    except ValueError:
        valid = False
    print("SAFECheck healthy" if valid else "SAFECheck unhealthy")
    sys.exit(0 if valid else 1)


if __name__ == "__main__":
    run()

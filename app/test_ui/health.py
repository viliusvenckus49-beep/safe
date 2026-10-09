"""Small polling health probe; do not import bot, database or Telegram libraries."""

import json
import os
from pathlib import Path
from time import time


def ready(path: Path, *, current_time: float | None = None) -> bool:
    heartbeat = path.parent / "heartbeat"
    moment = time() if current_time is None else current_time
    try:
        if not heartbeat.is_file() or not 0 <= moment - heartbeat.stat().st_mtime <= 180:
            return False
        if not path.is_file() or path.stat().st_size > 2_000_000:
            return False
        design = json.loads(path.read_text())
        return (
            isinstance(design, dict)
            and design.get("version") == 1
            and isinstance(design.get("texts"), dict)
            and isinstance(design.get("layouts"), dict)
        )
    except (OSError, ValueError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if ready(Path(os.getenv("TEST_UI_STATE_FILE", "/state/design.json"))) else 1)

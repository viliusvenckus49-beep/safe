"""Load only designated credentials, then replace this process with the application."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

ALLOWED = frozenset({"BOT_TOKEN", "DATABASE_URL", "REDIS_URL", "TELEGRAM_PROXY_URL"})


def load_secrets(path: Path = Path("/run/secrets/runtime_env")) -> None:
    values = dotenv_values(path, interpolate=False)
    if not {"BOT_TOKEN", "DATABASE_URL", "REDIS_URL"} <= values.keys():
        raise ValueError("Required runtime credential missing")
    if values.keys() - ALLOWED or any(not value for value in values.values()):
        raise ValueError("Invalid runtime credential file")
    for key, value in values.items():
        assert value is not None
        os.environ[key] = value


def main() -> None:
    try:
        load_secrets()
        if len(sys.argv) < 2:
            raise ValueError("Application command missing")
    except Exception:
        print("SAFECheck runtime credential setup failed.", file=sys.stderr)
        raise SystemExit(2) from None
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()

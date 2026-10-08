#!/usr/bin/env python3
"""Install the existing health monitor timer; preserve private alert configuration."""

import os
import subprocess
from pathlib import Path


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Monitor installation requires root")
    root = Path(__file__).resolve().parent.parent
    destination = Path("/opt/safecheck-monitor")
    destination.mkdir(mode=0o755, parents=True, exist_ok=True)
    wrapper = destination / "monitor_runtime.py"
    wrapper.write_bytes((root / "deploy/monitor_runtime.py").read_bytes())
    wrapper.chmod(0o755)
    for name in ("safecheck-monitor.service", "safecheck-monitor.timer"):
        unit = Path("/etc/systemd/system") / name
        unit.write_bytes((root / "deploy/systemd" / name).read_bytes())
        unit.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "safecheck-monitor.timer"], check=True)
    subprocess.run(["systemctl", "start", "safecheck-monitor.service"], check=True)
    print("SAFECheck monitor enabled; aggregate history recorded every two minutes.")


if __name__ == "__main__":
    main()

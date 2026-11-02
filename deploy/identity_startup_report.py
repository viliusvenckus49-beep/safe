"""Report only service status and safe startup diagnostics, without credentials."""

import json
import subprocess


def main():
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    row = json.loads(
        subprocess.check_output(docker + ["inspect", "safecheck-bot-1"], stderr=subprocess.DEVNULL)
    )[0]
    print(
        "IdentityStartupState="
        + json.dumps(
            {
                "image": row["Config"]["Image"],
                "running": row["State"]["Running"],
                "health": row["State"].get("Health", {}).get("Status"),
                "restart_count": row["RestartCount"],
            }
        ),
        flush=True,
    )
    result = subprocess.run(
        docker + ["logs", "--tail", "100", "safecheck-bot-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout + result.stderr).splitlines():
        try:
            value = json.loads(line)
            keys = ("event", "exception_type", "operation")
            safe = {k: value[k] for k in keys if k in value}
            if safe:
                print("IdentityStartupEvent=" + json.dumps(safe), flush=True)
        except (ValueError, TypeError):
            if line.startswith("SAFECheck startup") or line.startswith("SAFECheck configuration"):
                print("IdentityStartupText=" + line, flush=True)
    # File metadata only; no session/API credential contents.
    for mount in row.get("Mounts", []):
        if mount["Destination"] == "/run/mtproto":
            import stat
            from pathlib import Path

            root = Path(mount["Source"])
            for file in (root, root / "api.json", root / "account.session"):
                info = file.lstat()
                print(
                    "IdentitySessionFile="
                    + json.dumps(
                        {
                            "path": str(file),
                            "uid": info.st_uid,
                            "gid": info.st_gid,
                            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("IdentityStartupReport=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

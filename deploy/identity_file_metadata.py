"""Read-only file metadata diagnostics; never open runtime credential contents."""

import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path


def metadata(path):
    try:
        info = path.lstat()
        kind = (
            "symlink"
            if stat.S_ISLNK(info.st_mode)
            else "file"
            if stat.S_ISREG(info.st_mode)
            else "directory"
            if stat.S_ISDIR(info.st_mode)
            else "other"
        )
        result = {
            "path": str(path),
            "type": kind,
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "uid": info.st_uid,
            "gid": info.st_gid,
        }
        if kind == "symlink":
            result["target"] = os.readlink(path)
        return result
    except OSError:
        return {"path": str(path), "unavailable": True}


def main():
    compose = Path("/etc/safecheck/compose.env")
    print("IdentityFileMetadata=" + json.dumps(metadata(compose)), flush=True)
    # This selector file contains only nonsecret paths/image settings.
    selectors = compose.read_text()
    paths = []
    for key in ("SAFECHECK_ENV_FILE", "SAFECHECK_SECRETS_DIR"):
        found = re.findall(r"^\s*" + key + r"\s*=([^\r\n]*)", selectors, re.M)
        if len(found) != 1:
            raise RuntimeError("Selector unavailable")
        parts = shlex.split(found[0], comments=True)
        if len(parts) != 1 or not Path(parts[0]).is_absolute():
            raise RuntimeError("Invalid selector path")
        paths.append(
            Path(parts[0]) / "runtime.env" if key.endswith("SECRETS_DIR") else Path(parts[0])
        )
    rows = json.loads(
        subprocess.check_output(
            ["docker", "--host=unix:///var/run/docker.sock", "inspect", "safecheck-bot-1"],
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    )
    for mount in rows[0].get("Mounts", []):
        if mount["Destination"] == "/run/secrets/runtime_env":
            paths.append(Path(mount["Source"]))
    seen = {compose}
    for path in paths:
        for entry in (path, *path.parents):
            if entry not in seen:
                print("IdentityFileMetadata=" + json.dumps(metadata(entry)), flush=True)
                seen.add(entry)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("IdentityFileMetadata=FAILED; no credential contents read", flush=True)
        raise SystemExit(1) from None

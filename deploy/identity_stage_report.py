"""Inspect private prepared identity metadata and retained work; never disclose credentials."""

import hashlib
import json
import re
import stat
import types
from pathlib import Path

HELPER_SHA = "3f1056df87dc58cad9fe7413d0cf6c8499d3b375fd1d74aeadca9fbbf454b462"


def private(path, directory=False):
    row = path.lstat()
    valid = stat.S_ISDIR(row.st_mode) if directory else stat.S_ISREG(row.st_mode)
    if not valid or row.st_uid != 0 or row.st_mode & 0o077:
        raise RuntimeError("Untrusted private stage")


def main():
    path = Path("/home/ubuntu/SAFECHECK_IDENTITY.py")
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != HELPER_SHA:
        raise RuntimeError("Operator helper checksum differs")
    helper = types.ModuleType("verified_identity_prepare")
    exec(compile(source, "<verified identity helper>", "exec"), helper.__dict__)
    root = Path("/var/lib/safecheck/identity-change")
    if not root.exists():
        print("IdentityStage=NOT_PREPARED", flush=True)
        return
    private(root, True)
    stages = []
    for stage in sorted(root.iterdir()):
        if not re.fullmatch(r"[a-f0-9]{32}", stage.name) or not (stage / "manifest.json").exists():
            continue
        private(stage, True)
        private(stage / "manifest.json")
        manifest = json.loads((stage / "manifest.json").read_text())
        for filename in (
            "settings.before",
            "credentials.before",
            "settings.next",
            "credentials.next",
        ):
            file = stage / filename
            private(file)
            if hashlib.sha256(file.read_bytes()).hexdigest() != manifest["sha256"][filename]:
                raise RuntimeError("Staged checksum differs")
        settings_before = (stage / "settings.before").read_bytes()
        credentials_before = (stage / "credentials.before").read_bytes()
        settings = (stage / "settings.next").read_text()
        credentials = (stage / "credentials.next").read_text()
        token = helper.scalar(credentials, "BOT_TOKEN")
        unchanged = (
            helper.scalar(settings_before.decode(), "ADMIN_IDS")
            == helper.scalar(settings, "ADMIN_IDS")
            and settings
            == helper.changed(
                settings_before.decode(), "GROUP_OWNER_ID", str(manifest["new_owner"])
            )
            and credentials == helper.changed(credentials_before.decode(), "BOT_TOKEN", token)
        )
        active_match = (
            Path(manifest["settings_path"]).read_bytes() == settings_before
            and Path(manifest["credentials_path"]).read_bytes() == credentials_before
        )
        stages.append(
            {
                "path": str(stage),
                "new_owner": manifest["new_owner"],
                "identity": manifest["identity"],
                "mtproto_enabled": manifest["mtproto_enabled"],
                "staged_hashes_verified": True,
                "only_token_owner_changed": unchanged,
                "active_originals_match": active_match,
            }
        )
    print("IdentityStages=" + json.dumps(stages, sort_keys=True), flush=True)
    docker = helper.DOCKER
    inventory = json.loads(
        helper.run(
            docker + ["inspect", "safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"]
        )
    )
    for row in inventory:
        print(
            "IdentityService="
            + json.dumps(
                {
                    "name": row["Name"],
                    "image": row["Config"]["Image"],
                    "id": row["Id"],
                    "started": row["State"]["StartedAt"],
                    "running": row["State"]["Running"],
                    "health": row["State"].get("Health", {}).get("Status"),
                }
            ),
            flush=True,
        )
    sql = helper.run(
        docker
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
            helper.COUNTS,
        ]
    ).decode()
    counts = json.loads(next(line for line in sql.splitlines() if line.startswith("{")))
    print("IdentityWork=" + json.dumps(counts, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("IdentityStageReport=FAILED; credential contents withheld", flush=True)
        raise SystemExit(1) from None

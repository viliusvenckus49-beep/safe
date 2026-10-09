#!/usr/bin/env python3
"""Deploy only the isolated UI preview service. Never update production Compose."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG = Path("/etc/safecheck-ui-studio/runtime.env")
DATA = Path("/var/lib/safecheck-ui-studio")
PRODUCTION = ("safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1")


def run(args, *, input_data=None, timeout=600, merged=False):
    completed = subprocess.run(args, input=input_data, capture_output=True, timeout=timeout)
    if completed.returncode:
        # Captured output may contain credentials; never echo it on failure.
        raise RuntimeError("UI deployment command failed: " + args[0])
    return completed.stdout + completed.stderr if merged else completed.stdout


def private_write(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, filename = tempfile.mkstemp(dir=path.parent, prefix=".ui-config-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def production_snapshot():
    return run(
        ["docker", "inspect", "--format", "{{.Id}} {{.Image}} {{.State.StartedAt}}", *PRODUCTION]
    )


def production_identity():
    # Read only the numeric bot identity and configured administrator IDs, never its token.
    code = "import runpy,json; runpy.run_path('/run/configs/secret_entrypoint.py')['load_secrets'](); from app.config import Settings; s=Settings(); print(json.dumps({'bot_id':int(s.bot_token.get_secret_value().split(':')[0]),'admins':s.admin_ids}))"
    return json.loads(run(["docker", "exec", "safecheck-bot-1", "python", "-c", code]))


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2 or not re.fullmatch(r"[0-9a-f]{40}", sys.argv[1]):
        raise RuntimeError("Root and an exact tested commit SHA are required")
    token = sys.stdin.buffer.read(1024).decode().strip()
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        raise RuntimeError("Missing or invalid SAFECHECK_TEST_BOT_TOKEN")
    identity = production_identity()
    if int(token.split(":")[0]) == identity["bot_id"]:
        raise RuntimeError("Test token belongs to the production bot; refused")
    admins = identity["admins"]
    if not admins or not all(
        part.strip().isdigit() and int(part.strip()) > 0 for part in admins.split(",")
    ):
        raise RuntimeError("Production administrator allowlist is unavailable")
    before = production_snapshot()
    root = Path(__file__).resolve().parent.parent
    image = "safecheck-ui:" + sys.argv[1][:12]
    run(["docker", "build", "-t", image, str(root)])
    print("UI candidate image built.", flush=True)
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(DATA, 10001, 10001)
    design = DATA / "design.json"
    if design.exists():
        backup = DATA / ("design-backup-" + str(time.time_ns()) + ".json")
        shutil.copy2(design, backup)
        os.chmod(backup, 0o600)
        os.chown(backup, 10001, 10001)
    previous = CONFIG.read_bytes() if CONFIG.exists() else None
    config = (
        f"TEST_UI_BOT_TOKEN={token}\nTEST_UI_ADMIN_IDS={admins}\nSAFECHECK_TEST_IMAGE={image}\n"
    ).encode()
    dc = [
        "docker",
        "compose",
        "--project-name",
        "safecheck-ui-studio",
        "--env-file",
        str(CONFIG),
        "-f",
        str(root / "docker-compose.test-ui.yml"),
    ]
    private_write(CONFIG, config)
    try:
        resolved = json.loads(run(dc + ["config", "--format", "json"]))
        if set(resolved["services"]) != {"studio"}:
            raise RuntimeError("UI-only deployment refused: unexpected services")
        # Render before polling starts, in a separate offline process. Loading another bot
        # process inside the live container would exceed its deliberately small memory limit.
        probe = "from pathlib import Path; from app.test_ui.profile import Design; from app.test_ui.preview import screen,SCREENS; d=Design(Path('/state/design.json')); assert len(SCREENS)==8; [(screen(n,d,l)) for l in ('lt','en','ru') for n in SCREENS]; print('UIStudioPreviewsVerified=24 ProductionDatabase=disconnected Moderation=disabled')"
        print(
            run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--memory",
                    "256m",
                    "--cpus",
                    "0.4",
                    "--pids-limit",
                    "128",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--mount",
                    f"type=bind,src={DATA},dst=/state,readonly",
                    image,
                    "python",
                    "-c",
                    probe,
                ]
            )
            .decode()
            .strip()
        )
        run(dc + ["up", "-d", "--no-deps", "studio"])
        for _ in range(60):
            status = (
                run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.State.Health.Status}}",
                        "safecheck-ui-studio-studio-1",
                    ]
                )
                .decode()
                .strip()
            )
            if status == "healthy":
                break
            time.sleep(2)
        else:
            raise RuntimeError("UI studio failed its polling health check")
        if production_snapshot() != before:
            raise RuntimeError("Production service identity changed during UI deployment")
        print("ProductionContainersUnchanged=true", flush=True)
        print("UIStudioImage=" + image + " Health=healthy", flush=True)
        # Read only the public bot username from structured startup logging.
        logs = run(["docker", "logs", "--tail", "120", "safecheck-ui-studio-studio-1"], merged=True)
        for line in logs.decode().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("event") == "test_ui_started" and re.fullmatch(
                r"[A-Za-z0-9_]+", str(event.get("username", ""))
            ):
                print("UIStudioBot=https://t.me/" + event["username"], flush=True)
    except Exception:
        if previous is not None:
            private_write(CONFIG, previous)
            run(dc + ["up", "-d", "--no-deps", "studio"])
        else:
            run(dc + ["stop", "studio"])
            CONFIG.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "UI studio deployment failed: " + str(error)
            if type(error) is RuntimeError
            else "UI studio deployment failed: " + type(error).__name__,
            file=sys.stderr,
        )
        raise SystemExit(1) from None

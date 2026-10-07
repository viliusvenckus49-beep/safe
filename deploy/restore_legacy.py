#!/usr/bin/env python3
"""Stage and atomically merge a sealed legacy snapshot; keep both databases backed up."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in {"plan", "apply"}:
        raise SystemExit("Mode, encrypted payload and expected source digest are required")
    mode, envelope, digest = sys.argv[1:]
    code_root = Path(__file__).resolve().parent.parent
    ops_module = load_module("ops", code_root / "deploy/ops.py")
    sealed = load_module("sealed", code_root / "deploy/sealed_data.py")
    bootstrap = ops_module.Operations(
        ops_module.parser().parse_args(["--root", str(code_root), "backup"])
    )
    current_root = Path(
        bootstrap.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                "safecheck-bot-1",
            ]
        )
        .decode()
        .strip()
    )
    args = ops_module.parser().parse_args(["--root", str(current_root), "backup"])
    ops = ops_module.Operations(args)
    private_dir = args.state_dir / "data-transfer"
    ops_module.private_directory(private_dir)
    source = private_dir / "source.json"
    if not source.exists() or sealed.snapshot_digest(source.read_bytes()) != digest:
        sealed.unseal(Path(envelope), private_dir / "receiver.pem", source, digest)
    image = (
        ops.run(["docker", "inspect", "--format", "{{.Image}}", "safecheck-bot-1"]).decode().strip()
    )
    verifier = "safecheck-merge-" + uuid4().hex
    created, stopped, applied = False, False, False
    with ops.lock(maintenance=mode == "apply"):
        try:
            if mode == "apply":
                ops.dc("stop", "--timeout", "45", "bot")
                stopped = True
            archive = ops.backup()
            ops.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    verifier,
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw",
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    ops_module.VERIFY_IMAGE,
                ]
            )
            created = True
            ops.run(
                [
                    "docker",
                    "exec",
                    verifier,
                    "sh",
                    "-ec",
                    "for i in $(seq 1 60); do pg_isready -h 127.0.0.1 -U postgres >/dev/null && exit 0; sleep 1; done; exit 1",
                ]
            )
            ops.run(["docker", "exec", verifier, "createdb", "-U", "postgres", "restore_check"])
            with archive.open("rb") as file:
                ops.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        verifier,
                        "pg_restore",
                        "--exit-on-error",
                        "--no-owner",
                        "--no-acl",
                        "-U",
                        "postgres",
                        "-d",
                        "restore_check",
                    ],
                    stdin=file,
                )
            stage_command = [
                "docker",
                "run",
                "--rm",
                "--user",
                "0",
                "--network",
                "container:" + verifier,
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                "type=bind,src=" + str(source) + ",dst=/private/source.json,readonly",
                "--mount",
                "type=bind,src=" + str(code_root) + ",dst=/tools,readonly",
                "-e",
                "DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1/restore_check",
                image,
                "python",
                "/tools/deploy/merge_database.py",
                "apply",
                "/private/source.json",
            ]
            # Only this subprocess can emit merge conflict codes, never SQL values or records.
            result = subprocess.run(
                stage_command, capture_output=True, timeout=1800, stdin=subprocess.DEVNULL
            )
            if result.returncode:
                safe = result.stderr.decode(errors="replace").strip()
                if safe.startswith("Merge refused:") and len(safe) < 100:
                    print(safe, flush=True)
                raise ops_module.OperationError("Staging merge failed; live records unchanged")
            stage = json.loads(result.stdout)
            ops_module.private_write(
                private_dir / "last-plan.json", json.dumps(stage, sort_keys=True)
            )
            print(
                "Isolated staging constraints passed: " + json.dumps(stage, sort_keys=True),
                flush=True,
            )
            if mode == "plan":
                return
            live = ops.dc(
                "run",
                "--rm",
                "--no-deps",
                "--user",
                "0",
                "--volume",
                str(source) + ":/private/source.json:ro",
                "--volume",
                str(code_root / "deploy/merge_database.py") + ":/tools/merge_database.py:ro",
                "--entrypoint",
                "python",
                "bot",
                "/run/configs/secret_entrypoint.py",
                "python",
                "/tools/merge_database.py",
                "apply",
                "/private/source.json",
                "--expected-target-digest",
                stage["target_digest"],
            )
            outcome = json.loads(live)
            applied = True
            ops.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")
            stopped = False
            print(
                "Live merge committed; bot is healthy: " + json.dumps(outcome, sort_keys=True),
                flush=True,
            )
            # Repeating the payload remains idempotent; retire the one-time transfer private key.
            (private_dir / "receiver.pem").unlink(missing_ok=True)
        except Exception:
            if applied:
                ops.args.archive = archive
                ops.args.confirm_restore = "safecheck"
                ops.restore()
                stopped = False
                print("Pre-merge database restored after a failed health check.", flush=True)
            raise
        finally:
            if created:
                ops.run(["docker", "rm", "-f", verifier])
            if stopped:
                ops.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Legacy data merge did not complete; inspect private plan and service health"
        ) from None

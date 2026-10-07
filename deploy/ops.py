#!/usr/bin/env python3
"""Small Ubuntu/Docker operations CLI. No Telegram calls unless alert options are explicit."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

ARCHIVE_NAME = re.compile(r"safecheck-\d{8}T\d{6}Z-[0-9a-f]{8}\.dump")
VERIFY_IMAGE = "postgres:17.6-alpine"


def pinned_image(image):
    return bool(
        re.search(r":\d[A-Za-z0-9._-]*$", image)
        or re.search(r"@sha256:[0-9a-f]{64}$", image)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", image)
    )


class OperationError(RuntimeError):
    pass


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OperationError("Directory must be owned by the operations user, without symlinks")
    path.chmod(0o700)


def private_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
    try:
        with open(temporary, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


class Operations:
    def __init__(self, args, runner=subprocess.run):
        self.args = args
        self.runner = runner
        self.compose = [
            "docker",
            "compose",
            "--project-name",
            args.project,
            "--env-file",
            str(args.env_file),
            "-f",
            str(args.root / "docker-compose.production.yml"),
        ]
        self.backups = args.backup_dir
        self.state = args.state_dir

    def run(self, command, *, stdin=None, stdout=None, env=None):
        if self.args.dry_run:
            print(shlex.join(command))
            return b""
        try:
            result = self.runner(
                command,
                stdin=stdin,
                stdout=stdout or subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=1800,
                env=env,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OperationError("Operations command unavailable or timed out") from error
        if result.returncode:
            # Docker/app stderr can contain DSNs or tokens. Do not include it in alerts/logs.
            raise OperationError(f"Operations command failed (exit {result.returncode})")
        return result.stdout or b""

    def dc(self, *args, **kwargs):
        return self.run(self.compose + list(args), **kwargs)

    @contextmanager
    def lock(self, *, maintenance=False):
        if self.args.dry_run:
            yield
            return
        private_directory(self.state)
        with open(
            self.state / "operations.lock",
            "a",
            opener=lambda name, flags: os.open(name, flags, 0o600),
        ) as file:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise OperationError("Another operation is running") from error
            marker = self.state / "maintenance"
            if maintenance:
                private_write(marker, str(time.time()))
            try:
                yield
            finally:
                if maintenance:
                    marker.unlink(missing_ok=True)

    def backup(self, *, protected=()):
        name = f"safecheck-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.dump"
        target = self.backups / name
        command = [
            "exec",
            "-T",
            "postgres",
            "pg_dump",
            "-U",
            "safecheck",
            "-d",
            "safecheck",
            "-Fc",
            "--no-owner",
            "--no-acl",
        ]
        if self.args.dry_run:
            self.dc(*command)
            self.verify(target)
            if self.args.offsite_command:
                self.run([str(self.args.offsite_command), str(target), str(target) + ".sha256"])
            return target
        private_directory(self.backups)
        temporary = target.with_suffix(".part")
        try:
            with open(
                temporary, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)
            ) as file:
                self.dc(*command, stdout=file)
                file.flush()
                os.fsync(file.fileno())
            if not temporary.stat().st_size:
                raise OperationError("Empty database archive")
            temporary.replace(target)
            private_write(Path(str(target) + ".sha256"), digest(target) + "\n")
            self.verify(target)
            if self.args.offsite_command:
                self.run([str(self.args.offsite_command), str(target), str(target) + ".sha256"])
            self.prune(protected=protected)
        finally:
            temporary.unlink(missing_ok=True)
        print(f"Verified backup: {target}")
        return target

    def prune(self, *, protected=()):
        cutoff = time.time() - self.args.retention_days * 86400
        for path in self.backups.iterdir():
            if path.resolve() in {item.resolve() for item in protected}:
                continue
            if ARCHIVE_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    Path(str(path) + ".sha256").unlink(missing_ok=True)

    def validate_archive(self, archive):
        if self.args.dry_run:
            return
        if archive.is_symlink() or not archive.is_file():
            raise OperationError("Archive must be a regular file, without a symlink")
        checksum = Path(str(archive) + ".sha256")
        if checksum.is_symlink() or not checksum.is_file():
            raise OperationError("Archive checksum missing")
        if checksum.read_text().strip() != digest(archive):
            raise OperationError("Archive checksum mismatch")

    def verify(self, archive):
        """Fully restore in a separate container; never create a database in the live cluster."""
        self.validate_archive(archive)
        container = "safecheck-verify-" + uuid4().hex
        created = False
        try:
            self.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    container,
                    "--network",
                    "none",
                    "--tmpfs",
                    "/var/lib/postgresql/data:rw",
                    "--env",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    VERIFY_IMAGE,
                ]
            )
            created = True
            # This single command retries inside the disposable container for at most 60s.
            self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "sh",
                    "-ec",
                    "for i in $(seq 1 60); do pg_isready -h 127.0.0.1 -U postgres >/dev/null && exit 0; "
                    "sleep 1; done; exit 1",
                ]
            )
            self.run(["docker", "exec", container, "createdb", "-U", "postgres", "restore_check"])
            command = [
                "docker",
                "exec",
                "-i",
                container,
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "-U",
                "postgres",
                "-d",
                "restore_check",
            ]
            if self.args.dry_run:
                self.run(command)
            else:
                with archive.open("rb") as file:
                    self.run(command, stdin=file)
            result = self.run(
                [
                    "docker",
                    "exec",
                    container,
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "restore_check",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-Atc",
                    "SELECT (to_regclass('public.users') IS NOT NULL) "
                    "AND EXISTS (SELECT 1 FROM public.alembic_version);",
                ]
            )
            if not self.args.dry_run and result.strip() != b"t":
                raise OperationError("Restored database is missing SAFECheck schema metadata")
        finally:
            if created:
                self.run(["docker", "rm", "-f", container])

    def validate_compose(self):
        self.dc("config", "--quiet")
        raw = self.dc("config", "--format", "json")
        if self.args.dry_run:
            return "safecheck:2.13.1"
        config = json.loads(raw)
        images = [service["image"] for service in config["services"].values()]
        if any(not pinned_image(image) for image in images):
            raise OperationError("Every production image must have an explicit version or digest")
        image = config["services"]["bot"]["image"]
        self.run(["docker", "image", "inspect", image])
        return image

    def readiness(self, *, env=None):
        self.dc("run", "--rm", "--no-deps", "bot", "python", "-m", "app.main", "--check", env=env)

    def start(self):
        self.validate_compose()
        self.dc("up", "-d", "--no-recreate", "--wait", "--wait-timeout", "120", "postgres", "redis")
        self.readiness()
        self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")

    def initialize(self):
        self.validate_compose()
        self.dc("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis")
        existing = self.dc(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "safecheck",
            "-d",
            "safecheck",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';",
        )
        if not self.args.dry_run and existing.strip() != b"0":
            raise OperationError(
                "Initialize requires an empty database; use backup-first deploy for existing data"
            )
        self.dc("run", "--rm", "--no-deps", "migrate")
        self.readiness()
        self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")

    def deploy(self):
        image = self.validate_compose()
        # Never silently replace infrastructure as part of an application update.
        status, _ = self.status()
        if not self.args.dry_run and any(
            status.get(name) != "healthy" for name in ("postgres", "redis")
        ):
            raise OperationError("PostgreSQL and Redis must already be healthy before update")
        previous = self.dc("ps", "-q", "bot").decode().strip()
        old_image = ""
        if previous:
            old_image = (
                self.run(["docker", "inspect", "--format", "{{.Image}}", previous]).decode().strip()
            )
        self.dc("stop", "--timeout", "45", "bot")
        try:
            archive = self.backup()
        except Exception:
            if previous:
                self.run(["docker", "start", previous])
            raise
        if not self.args.dry_run:
            private_write(
                self.state / "last-deployment.json",
                json.dumps(
                    {
                        "previous_image": old_image,
                        "candidate_image": image,
                        "backup": str(archive),
                        "started_at": datetime.now(UTC).isoformat(),
                    }
                )
                + "\n",
            )
        self.dc("run", "--rm", "--no-deps", "migrate")
        self.readiness()
        self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")

    def restore(self):
        if self.args.confirm_restore != "safecheck":
            raise OperationError("Destructive restore requires --confirm-restore safecheck")
        archive = self.args.archive
        self.verify(archive)
        status, _ = self.status()
        if not self.args.dry_run and any(
            status.get(name) != "healthy" for name in ("postgres", "redis")
        ):
            raise OperationError("PostgreSQL and Redis must be healthy before restore")
        self.dc(
            "exec",
            "-T",
            "redis",
            "sh",
            "-ec",
            'REDISCLI_AUTH="$(cat /run/secrets/redis_maintenance_password)" '
            "redis-cli --user maintenance -n 0 PING | grep -qx PONG",
        )
        self.dc("stop", "--timeout", "45", "bot")
        try:
            self.backup(protected=(archive,))  # Keep current data and the selected restore archive.
        except Exception:
            self.dc("start", "bot")  # No database or FSM changes have occurred yet.
            raise
        self.dc("exec", "-T", "postgres", "dropdb", "-U", "safecheck", "--force", "safecheck")
        self.dc(
            "exec", "-T", "postgres", "createdb", "-U", "safecheck", "-O", "safecheck", "safecheck"
        )
        command = [
            "exec",
            "-T",
            "postgres",
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-acl",
            "-U",
            "safecheck",
            "-d",
            "safecheck",
        ]
        if self.args.dry_run:
            self.dc(*command)
        else:
            with archive.open("rb") as file:
                self.dc(*command, stdin=file)
        self.dc(
            "exec",
            "-T",
            "redis",
            "sh",
            "-ec",
            'REDISCLI_AUTH="$(cat /run/secrets/redis_maintenance_password)" '
            "redis-cli --user maintenance -n 0 FLUSHDB | grep -qx OK",
        )
        self.readiness()  # Fail closed if this image and the restored migration head differ.
        self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")

    def rollback(self):
        if not self.args.image:
            raise OperationError("Rollback requires an explicit previous image")
        if not pinned_image(self.args.image):
            raise OperationError("Rollback requires an explicit version or SHA256 digest")
        self.run(["docker", "image", "inspect", self.args.image])
        env = dict(os.environ, SAFECHECK_IMAGE=self.args.image)
        self.readiness(env=env)  # Refuse rollback across incompatible migration heads.
        self.persist_image(self.args.image)
        self.dc("stop", "--timeout", "45", "bot")
        self.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot", env=env)

    def persist_image(self, image):
        """Keep a checked rollback selected across later starts and host reboots."""
        if self.args.dry_run:
            print(f"Persist application image selector in {self.args.env_file}")
            return
        path = self.args.env_file
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise OperationError("Compose env must be an owned regular file, without a symlink")
        content = path.read_text()
        selector = re.compile(r"^[ \t]*(?:export[ \t]+)?SAFECHECK_IMAGE[ \t]*=.*$", re.MULTILINE)
        if len(selector.findall(content)) > 1:
            raise OperationError("Compose env has duplicate application image selectors")
        replacement = "SAFECHECK_IMAGE=" + image
        if selector.search(content):
            content = selector.sub(lambda match: replacement, content)
        else:
            content = content.rstrip("\n") + "\n" + replacement + "\n"
        private_write(path, content)

    def status(self):
        raw = self.dc("ps", "--all", "--format", "json")
        if self.args.dry_run:
            return {}, True
        decoded = raw.decode().strip()
        rows = (
            json.loads(decoded)
            if decoded.startswith("[")
            else [json.loads(line) for line in decoded.splitlines()]
        )
        states = {}
        for row in rows:
            if row.get("Service") in {"bot", "postgres", "redis"}:
                states[row["Service"]] = row.get("Health") or row.get("State", "missing")
        for name in ("bot", "postgres", "redis"):
            states.setdefault(name, "missing")
        healthy = all(value == "healthy" for value in states.values())
        try:
            self.readiness()
        except OperationError:
            states["readiness"] = "failed"
            healthy = False
        else:
            states["readiness"] = "passed"
        return states, healthy

    def monitor(self):
        marker = self.state / "maintenance"
        if (
            not self.args.dry_run
            and marker.exists()
            and time.time() - marker.stat().st_mtime < 3600
        ):
            print("SAFECheck maintenance in progress")
            return True
        try:
            states, healthy = self.status()
        except OperationError:
            states, healthy = {"docker": "unavailable"}, False
        if marker.exists() and time.time() - marker.stat().st_mtime >= 3600:
            states["maintenance"] = "stale"
            healthy = False
        metrics = {}
        if states.get("bot") == "healthy":
            try:
                raw = self.dc(
                    "exec",
                    "-T",
                    "bot",
                    "python",
                    "/run/configs/secret_entrypoint.py",
                    "python",
                    "-m",
                    "app.operations_status",
                )
                metrics = json.loads(raw)
                expected = {
                    "ban_terminal",
                    "recovery_terminal",
                    "oldest_pending_seconds",
                    "pending_reports",
                    "pending_rep",
                }
                if metrics.keys() != expected or any(
                    type(value) is not int or value < 0 for value in metrics.values()
                ):
                    raise ValueError("Invalid aggregate metrics")
                states["outbox"] = (
                    "stale"
                    if metrics["oldest_pending_seconds"] > self.args.outbox_max_age
                    else "current"
                )
                healthy = healthy and states["outbox"] == "current"
            except (OperationError, ValueError, TypeError):
                states["outbox"] = "unavailable"
                healthy = False
        print(
            json.dumps(
                {
                    "healthy": healthy,
                    "services": states,
                    "metrics": metrics,
                    "offsite_hook": "configured" if self.args.offsite_command else "not configured",
                },
                sort_keys=True,
            )
        )
        if self.args.dry_run:
            return healthy
        private_directory(self.state)
        path = self.state / "monitor.json"
        previous = json.loads(path.read_text()) if path.exists() else {}
        signature = json.dumps(states, sort_keys=True)
        if bool(self.args.alert_token_file) != bool(self.args.alert_chat_id):
            raise OperationError("Alerts require both explicit token file and numeric chat ID")
        changed = previous.get("signature") != signature
        last_sent = previous.get("last_sent", 0)
        new_failures = any(
            metrics.get(key, 0) > previous.get("metrics", {}).get(key, 0)
            for key in ("ban_terminal", "recovery_terminal")
        )
        if self.args.alert_token_file and (
            changed or new_failures or (not healthy and time.time() - last_sent >= 3600)
        ):
            if not healthy or new_failures or previous.get("healthy") is False:
                message = (
                    "⚠️ SAFECheck: reikia dėmesio"
                    if not healthy or new_failures
                    else "✅ SAFECheck veikimas atkurtas"
                )
                self.notify(
                    message
                    + ": "
                    + signature
                    + "; terminal jobs: "
                    + json.dumps(
                        {key: metrics.get(key, 0) for key in ("ban_terminal", "recovery_terminal")}
                    )
                )
                last_sent = time.time()
        private_write(
            path,
            json.dumps(
                {
                    "signature": signature,
                    "healthy": healthy,
                    "last_sent": last_sent,
                    "metrics": metrics,
                }
            ),
        )
        return healthy

    def notify(self, message):
        info = self.args.alert_token_file.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise OperationError("Alert token must be an owned private regular file")
        token = self.args.alert_token_file.read_text().strip()
        if (
            not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token)
            or not self.args.alert_chat_id.isdigit()
            or int(self.args.alert_chat_id) <= 0
        ):
            raise OperationError("Invalid explicit alert configuration")
        payload = json.dumps({"chat_id": self.args.alert_chat_id, "text": message}).encode()
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + token + "/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = json.load(response)
            if not result.get("ok"):
                raise OperationError("Admin alert delivery failed")
        except Exception as error:
            raise OperationError("Admin alert delivery failed") from error


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    result.add_argument("--env-file", type=Path, default=Path("/etc/safecheck/compose.env"))
    result.add_argument("--project", default="safecheck")
    result.add_argument("--backup-dir", type=Path, default=Path("/var/backups/safecheck"))
    result.add_argument("--state-dir", type=Path, default=Path("/var/lib/safecheck"))
    result.add_argument("--retention-days", type=int, default=14)
    result.add_argument("--offsite-command", type=Path)
    result.add_argument("--alert-token-file", type=Path)
    result.add_argument("--alert-chat-id")
    result.add_argument(
        "--outbox-max-age",
        type=int,
        default=3600,
        help="Alert when due work is older than this many seconds",
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without Docker, writes, or notifications",
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("initialize", "start", "stop", "backup", "status", "monitor", "deploy"):
        commands.add_parser(name)
    commands.add_parser("verify").add_argument("archive", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--confirm-restore")
    commands.add_parser("rollback").add_argument("--image", required=True)
    return result


def main():
    args = parser().parse_args()
    if (
        args.retention_days < 1
        or args.outbox_max_age < 1
        or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", args.project)
    ):
        raise SystemExit("Invalid retention or project name")
    ops = Operations(args)
    try:
        if args.command == "monitor":
            raise SystemExit(0 if ops.monitor() else 1)
        if args.command == "status":
            states, healthy = ops.status()
            print(json.dumps(states, sort_keys=True))
            raise SystemExit(0 if healthy else 1)
        with ops.lock(
            maintenance=args.command
            in {"initialize", "start", "stop", "deploy", "restore", "rollback"}
        ):
            if args.command == "verify":
                ops.verify(args.archive)
            elif args.command == "stop":
                ops.dc("stop", "--timeout", "45", "bot", "redis", "postgres")
            else:
                getattr(ops, args.command)()
    except OperationError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print(
            "Operations failed; inspect private configuration and service state.", file=sys.stderr
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

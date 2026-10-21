#!/usr/bin/env python3
"""Backup-first 0020 release. No staging runtime, automatic DB restore or Redis reset."""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path
from uuid import uuid4

PINS = {
    "0010_redsafe_profile.py": "47431ef8207005336c163c73c611ecadf70dafdeb03597f7aedc76df5e2ae214",
    "0011_group_moderation.py": "9e8e28f9842dee7990043ddc42ce95ee6dc3ab1a3fff3dab0949be7f70249b8d",
    "0012_group_commands.py": "9f4c0fd8f71f1b693d6fb656d26d076784cc721a4a9f4db4bfd31dc4d9ce9ccd",
    "0013_ad_content.py": "3f8950cbe91ab655aafd7759b6e9629d83990144a0b3036b79915fd573589136",
    "0014_ad_publication.py": "08c05f8fbcca3e3fb576e5498d24768e758493744f4432443f52cee90ab1082f",
    "0015_global_ads_assignments_operations_and_.py": "1e1d787a2e15ed775d0798982063e50010573d91915c22af44c0d005d613d3d0",
    "0016_ad_customer_capabilities_and_hashed_.py": "07aa974edd1f6f74b6b284d2c77f3dba0e65cd3281f4b8fe59ebed7ab1fafd0c",
    "0017_warning_escalations_and_controls.py": "4c4d772494e884becd6445826e44b68f3a49425329deef148432b04d5956d433",
    "0018_warning_selection_and_private_receipts.py": "9325f46e77b37ce44dfb1d962526cd5d35acb02c7a3604f1328bff0fb31a36a4",
    "0019_linked_sanction_replacements.py": "34bb8d7b7c0acfb769e2a5cda05876b4a617501a3149a385ebcc94b1ab768747",
    "0020_unified_auto_ads.py": "66b92eb3ad9521adc564f18489081e9387a9afebbf38736489e8d08cbf50e98b",
}


def imported(path):
    spec = importlib.util.spec_from_file_location("production_ops", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrations(root):
    return {
        p.name: p.read_bytes()
        for p in (root / "migrations/versions").glob("*.py")
        if p.name != "__init__.py"
    }


def compatible(candidate, previous):
    if not PINS.keys() <= candidate.keys():
        return False
    if any(hashlib.sha256(candidate[name]).hexdigest() != digest for name, digest in PINS.items()):
        return False
    if any(candidate.get(name) != content for name, content in previous.items()):
        return False
    return candidate.keys() - previous.keys() <= PINS.keys()


def unchanged_data_services(before, after):
    for service in ("postgres", "redis"):
        if before["services"][service] != after["services"][service]:
            raise RuntimeError("Production data service configuration differs")
    for key in ("volumes", "secrets"):
        if before.get(key) != after.get(key):
            raise RuntimeError("Production volume or credential paths differ")


def data_fingerprint(row):
    # Docker constructs Mounts from a map: ordering is not a configuration change.
    result = {key: row[key] for key in ("Id", "Image", "Config", "HostConfig")}
    result["Mounts"] = sorted(row["Mounts"], key=lambda mount: mount["Destination"])
    return result


class Release:
    def __init__(self, root, sha):
        if not re.fullmatch(r"[0-9a-f]{40}", sha) or root.name != sha:
            raise RuntimeError("Use an immutable SHA release directory")
        self.root, self.sha = root, sha
        self.ops = imported(root / "deploy/ops.py")
        args = self.ops.parser().parse_args(["--root", str(root), "deploy"])
        self.command = self.ops.Operations(args)
        self.old = self.inspect("safecheck-bot-1")
        if not self.old["State"]["Running"]:
            raise RuntimeError("Original production bot is not running")
        labels = self.old["Config"].get("Labels", {})
        if labels.get("com.docker.compose.project") != "safecheck":
            raise RuntimeError("Unknown production Compose project")
        old_root = Path(labels["com.docker.compose.project.working_dir"])
        if not old_root.is_absolute() or not (old_root / "docker-compose.production.yml").is_file():
            raise RuntimeError("Missing production source")
        if not compatible(migrations(root), migrations(old_root)):
            raise RuntimeError("Unreviewed migration chain")
        old_args = self.ops.parser().parse_args(["--root", str(old_root), "deploy"])
        self.original = self.ops.Operations(old_args)
        self.version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", self.version):
            raise RuntimeError("Invalid release version")
        self.image = "safecheck:" + self.version + "-" + sha[:12]
        self.backup_dir = Path("/var/backups/safecheck") / ("production-0020-" + uuid4().hex)
        self.state = Path("/var/lib/safecheck") / ("production-0020-" + sha)
        self.fingerprints = {
            name: self.service_fingerprint(name)
            for name in ("safecheck-postgres-1", "safecheck-redis-1")
        }

    def run(self, args, **kwargs):
        return self.command.run(args, **kwargs)

    def inspect(self, name):
        return json.loads(self.run(["docker", "inspect", name]))[0]

    def service_fingerprint(self, name):
        row = self.inspect(name)
        if row.get("State", {}).get("Health", {}).get("Status") != "healthy":
            raise RuntimeError("Production data service is not healthy")
        return data_fingerprint(row)

    def preserve_services(self):
        for name, expected in self.fingerprints.items():
            actual = self.service_fingerprint(name)
            if actual != expected:
                fields = sorted(key for key in expected if expected[key] != actual[key])
                print(
                    "DataServiceGuard=FAIL; Service=" + name + "; Fields=" + ",".join(fields),
                    flush=True,
                )
                raise RuntimeError("Production database/cache container changed")

    def sql(self, statement):
        return (
            self.run(
                [
                    "docker",
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
                    "BEGIN READ ONLY; " + statement + "; COMMIT;",
                ]
            )
            .decode()
            .splitlines()
        )

    def idle(self):
        for table, condition in (
            ("group_sanction_jobs", "status IN ('PENDING','PROCESSING','UNCERTAIN')"),
            ("group_sanctions", "status IN ('PENDING','REVOKING','UNCERTAIN')"),
            ("ad_publication_attempts", "status IN ('CLAIMED','SENDING','UNCERTAIN')"),
        ):
            exists = self.sql("SELECT to_regclass('public." + table + "') IS NOT NULL")
            if "t" in exists and "0" not in self.sql(
                "SELECT count(*) FROM " + table + " WHERE " + condition
            ):
                raise RuntimeError("Unfinished or uncertain production work; reconcile first")

    def writers(self):
        ids = self.run(["docker", "ps", "-q"]).decode().split()
        for row in json.loads(self.run(["docker", "inspect", *ids])):
            if row["Id"] == self.old["Id"] or row["Id"] in {
                v["Id"] for v in self.fingerprints.values()
            }:
                continue
            production_networks = set(self.old["NetworkSettings"]["Networks"])
            if production_networks & set(row["NetworkSettings"]["Networks"]):
                raise RuntimeError("Another running container shares the production network")

    def prepare(self):
        self.idle()
        self.writers()
        if shutil.disk_usage(self.root).free < 2 * 1024**3:
            raise RuntimeError("Insufficient release disk space")
        available = next(
            int(line.split()[1]) * 1024
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:")
        )
        if available < 768 * 1024**2:
            raise RuntimeError("Insufficient release memory")
        self.run(["docker", "build", "-t", self.image, str(self.root)])
        self.ops.private_directory(self.state)
        self.ops.private_directory(self.backup_dir)
        self.ops.private_write(self.state / "compose.env", self.original.args.env_file.read_text())
        args = self.ops.parser().parse_args(
            ["--root", str(self.root), "--env-file", str(self.state / "compose.env"), "deploy"]
        )
        self.candidate = self.ops.Operations(args)
        self.candidate.persist_image(self.image)
        before = json.loads(self.original.dc("config", "--format", "json"))
        after = json.loads(self.candidate.dc("config", "--format", "json"))
        unchanged_data_services(before, after)
        self.candidate.validate_compose()
        identity = self.candidate.dc(
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--volume",
            str(self.root / "deploy/production_probe.py") + ":/run/probe.py:ro",
            "bot",
            "python",
            "/run/probe.py",
            "identity",
        )
        print("ProductionIdentity=" + json.dumps(json.loads(identity), sort_keys=True), flush=True)
        self.preserve_services()
        print(
            "ProductionPlan=PASS; Candidate="
            + self.image
            + "; SourceSchema="
            + str(self.sql("SELECT version_num FROM alembic_version")),
            flush=True,
        )

    def backup(self, label):
        path = self.backup_dir / (label + ".dump")
        with open(path, "xb", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
            self.original.dc(
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
                stdout=output,
            )
            output.flush()
            os.fsync(output.fileno())
        if not path.stat().st_size:
            raise RuntimeError("Empty production backup")
        self.ops.private_write(Path(str(path) + ".sha256"), self.ops.digest(path) + "\n")
        print("PrivateBackup=" + str(path) + "; SHA256=" + self.ops.digest(path), flush=True)
        return path

    def clone_probe(self, name, baseline=None):
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network",
            "container:" + name,
            "--mount",
            "type=bind,source="
            + str(self.root / "deploy/production_probe.py")
            + ",target=/run/probe.py,readonly",
            "-e",
            "BOT_TOKEN=123456:ISOLATED_REHEARSAL",
            "-e",
            "DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/restore_check",
            "-e",
            "ENVIRONMENT=development",
            "--entrypoint",
            "python",
            self.image,
            "/run/probe.py",
        ]
        return self.probe(command, baseline)

    def probe(self, command, baseline=None):
        with tempfile.TemporaryFile() as payload:
            if baseline is not None:
                payload.write(json.dumps(baseline).encode())
                payload.seek(0)
                command = [*command, "check"]
            return json.loads(self.run(command, stdin=payload))

    def rehearsal(self, archive):
        name = "safecheck-release-verify-" + uuid4().hex
        cid = None
        try:
            cid = (
                self.run(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        name,
                        "--label",
                        "safecheck.release=" + self.sha,
                        "--network",
                        "none",
                        "--tmpfs",
                        "/var/lib/postgresql/data:rw",
                        "--memory",
                        "256m",
                        "--cpus",
                        "0.5",
                        "-e",
                        "POSTGRES_HOST_AUTH_METHOD=trust",
                        "postgres:17.6-alpine",
                    ]
                )
                .decode()
                .strip()
            )
            self.run(
                [
                    "docker",
                    "exec",
                    name,
                    "sh",
                    "-ec",
                    "for i in $(seq 1 60); do pg_isready -h 127.0.0.1 -U postgres >/dev/null && exit 0; sleep 1; done; exit 1",
                ]
            )
            self.run(["docker", "exec", name, "createdb", "-U", "postgres", "restore_check"])
            with archive.open("rb") as dump:
                self.run(
                    [
                        "docker",
                        "exec",
                        "-i",
                        name,
                        "pg_restore",
                        "--exit-on-error",
                        "--no-owner",
                        "--no-acl",
                        "-U",
                        "postgres",
                        "-d",
                        "restore_check",
                    ],
                    stdin=dump,
                )
            baseline = self.clone_probe(name)
            self.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + name,
                    "--memory",
                    "384m",
                    "-e",
                    "BOT_TOKEN=123456:ISOLATED_REHEARSAL",
                    "-e",
                    "DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/restore_check",
                    "-e",
                    "ENVIRONMENT=development",
                    "--entrypoint",
                    "python",
                    self.image,
                    "-m",
                    "alembic",
                    "upgrade",
                    "head",
                ]
            )
            self.clone_probe(name, baseline)
            self.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "container:" + name,
                    "-e",
                    "BOT_TOKEN=123456:ISOLATED_REHEARSAL",
                    "-e",
                    "DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:5432/restore_check",
                    "-e",
                    "ENVIRONMENT=development",
                    "--entrypoint",
                    "python",
                    self.image,
                    "-m",
                    "alembic",
                    "check",
                ]
            )
            self.ops.private_write(
                archive.with_suffix(".retention.json"), json.dumps(baseline, sort_keys=True)
            )
            print(
                "IsolatedRestoreMigrationRetention=PASS; Tables=" + str(len(baseline["tables"])),
                flush=True,
            )
            return baseline
        finally:
            if cid:
                row = self.inspect(name)
                if row["Id"] != cid or row["Config"]["Labels"].get("safecheck.release") != self.sha:
                    raise RuntimeError("Rehearsal container ownership changed")
                self.run(["docker", "rm", "-f", cid])

    def apply(self):
        self.prepare()
        self.rehearsal(self.backup("online-rehearsal"))
        with self.original.lock(maintenance=True):
            if self.inspect("safecheck-bot-1")["Id"] != self.old["Id"]:
                raise RuntimeError("Production bot changed after preflight")
            self.preserve_services()
            self.writers()
            self.idle()
            self.original.dc("stop", "--timeout", "45", "bot")
            migrated = False
            candidate_started = False
            try:
                if self.inspect("safecheck-bot-1")["State"]["Running"]:
                    raise RuntimeError("Original production bot did not stop")
                self.idle()
                frozen = self.backup("frozen-before-migration")
                baseline = self.rehearsal(frozen)
                self.ops.private_write(
                    self.state / "release.json",
                    json.dumps(
                        {
                            "sha": self.sha,
                            "old_image": self.old["Image"],
                            "old_root": str(self.original.args.root),
                            "backup": str(frozen),
                            "status": "FROZEN",
                        }
                    ),
                )
                # From this boundary, failure retains the database and requires a forward recovery.
                migrated = True
                self.candidate.dc("run", "--rm", "--no-deps", "migrate")
                self.probe(
                    self.candidate.compose
                    + [
                        "run",
                        "--rm",
                        "-T",
                        "--no-deps",
                        "--volume",
                        str(self.root / "deploy/production_probe.py") + ":/run/probe.py:ro",
                        "bot",
                        "python",
                        "/run/probe.py",
                    ],
                    baseline,
                )
                self.candidate.readiness()
                candidate_started = True
                self.candidate.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")
                self.preserve_services()
                self.original.persist_image(self.image)
                self.ops.private_write(
                    self.state / "release.json",
                    json.dumps(
                        {
                            "sha": self.sha,
                            "old_image": self.old["Image"],
                            "backup": str(frozen),
                            "status": "HEALTHY",
                        }
                    ),
                )
            except BaseException:
                if not migrated:
                    self.run(["docker", "start", self.old["Id"]])
                    print(
                        "ProductionMigrationNotStarted=True; OriginalBotRestarted=True", flush=True
                    )
                elif candidate_started:
                    self.candidate.dc("stop", "--timeout", "45", "bot")
                    print(
                        "ProductionHeldForForwardRecovery=True; DatabaseAndRedisRetained=True",
                        flush=True,
                    )
                else:
                    print(
                        "ProductionHeldForForwardRecovery=True; Backup=" + str(self.backup_dir),
                        flush=True,
                    )
                raise
        print("PRODUCTION_RELEASE=HEALTHY; Schema=0020; Image=" + self.image, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sha")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Run through sudo with the existing private VPS configuration")
    os.umask(0o077)
    Release(Path(__file__).resolve().parent.parent, args.sha).apply()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "PRODUCTION_RELEASE=STOPPED; ExceptionType="
            + type(error).__name__
            + "; inspect private state; credentials and raw command output withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

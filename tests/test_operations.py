"""Operations tests use a fake Docker runner and never contact Docker or Telegram."""

import importlib.util
import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest


def load_deployment_module(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / "deploy" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


operations = load_deployment_module("ops")
OperationError, Operations, digest, parser = (
    operations.OperationError,
    operations.Operations,
    operations.digest,
    operations.parser,
)
load_secrets = load_deployment_module("secret_entrypoint").load_secrets
upload_adapter = load_deployment_module("upload_backup")


class FakeDocker:
    def __init__(self):
        self.commands = []
        self.fail = None
        self.states = {name: "healthy" for name in ("bot", "postgres", "redis")}
        self.metrics = {
            "ban_terminal": 0,
            "recovery_terminal": 0,
            "oldest_pending_seconds": 0,
            "pending_reports": 0,
            "pending_rep": 0,
        }
        self.restored = []
        self.table_count = b"0\n"

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.fail and self.fail(command):
            return subprocess.CompletedProcess(command, 1, b"", b"SECRET_SENTINEL")
        output = b""
        if "pg_dump" in command:
            kwargs["stdout"].write(b"PGDMP fake consistent snapshot")
        if "pg_restore" in command:
            self.restored.append(kwargs["stdin"].read())
        if "config" in command and "--format" in command:
            output = json.dumps(
                {
                    "services": {
                        "bot": {"image": "safecheck:2.13.0"},
                        "migrate": {"image": "safecheck:2.13.0"},
                        "postgres": {"image": "postgres:17.6-alpine"},
                        "redis": {"image": "redis:7.4.5-alpine"},
                    }
                }
            ).encode()
        if "ps" in command:
            output = (
                b"old-bot-container"
                if "-q" in command
                else json.dumps(
                    [{"Service": name, "Health": health} for name, health in self.states.items()]
                ).encode()
            )
        if "inspect" in command and "--format" in command:
            output = b"sha256:previousimage"
        if "psql" in command:
            output = self.table_count if "information_schema.tables" in command[-1] else b"t\n"
        if "app.operations_status" in command:
            output = json.dumps(self.metrics).encode()
        return subprocess.CompletedProcess(command, 0, output, b"")


def make_ops(tmp_path, command="backup", extra=()):
    args = parser().parse_args(
        [
            "--root",
            str(tmp_path / "checkout"),
            "--env-file",
            str(tmp_path / "compose.env"),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--state-dir",
            str(tmp_path / "state"),
            *extra,
            command,
        ]
    )
    fake = FakeDocker()
    return Operations(args, fake), fake


def test_backup_is_private_verified_and_prunes_only_owned_archive_names(tmp_path):
    ops, fake = make_ops(tmp_path)
    ops.backups.mkdir()
    old = ops.backups / "safecheck-20200101T000000Z-12345678.dump"
    old.write_bytes(b"old")
    os.utime(old, (0, 0))
    unrelated = ops.backups / "operator-notes.dump"
    unrelated.write_text("keep")
    archive = ops.backup()
    assert not old.exists()
    assert unrelated.exists()
    assert stat.S_IMODE(ops.backups.stat().st_mode) == 0o700
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    checksum = Path(str(archive) + ".sha256")
    assert stat.S_IMODE(checksum.stat().st_mode) == 0o600
    assert checksum.read_text().strip() == digest(archive)
    assert fake.restored == [archive.read_bytes()]
    isolated = next(cmd for cmd in fake.commands if cmd[:2] == ["docker", "run"])
    assert isolated[isolated.index("--network") + 1] == "none"
    assert "--tmpfs" in isolated
    readiness = next(cmd[-1] for cmd in fake.commands if "pg_isready" in cmd[-1])
    assert "-h 127.0.0.1" in readiness  # Skip the entrypoint's temporary socket-only server.
    assert not {"--publish", "-p", "--volume", "-v"} & set(isolated)
    assert fake.commands[-1][:3] == ["docker", "rm", "-f"]


def test_failed_dump_never_publishes_archive_or_leaks_error(tmp_path):
    ops, fake = make_ops(tmp_path)
    fake.fail = lambda command: "pg_dump" in command
    with pytest.raises(OperationError) as error:
        ops.backup()
    assert "SECRET_SENTINEL" not in str(error.value)
    assert list(ops.backups.iterdir()) == []


def test_verification_failure_removes_only_disposable_container(tmp_path):
    ops, fake = make_ops(tmp_path)
    fake.fail = lambda command: "pg_restore" in command
    with pytest.raises(OperationError):
        ops.backup()
    removal = fake.commands[-1]
    assert removal[:3] == ["docker", "rm", "-f"]
    assert removal[-1].startswith("safecheck-verify-")
    assert len([cmd for cmd in fake.commands if "rm" in cmd]) == 1


def test_checksum_mismatch_fails_before_any_docker_command(tmp_path):
    ops, fake = make_ops(tmp_path)
    archive = tmp_path / "archive.dump"
    archive.write_bytes(b"changed")
    Path(str(archive) + ".sha256").write_text("wrong")
    with pytest.raises(OperationError, match="checksum mismatch"):
        ops.verify(archive)
    assert fake.commands == []


@pytest.mark.parametrize(
    "command", ["initialize", "start", "backup", "deploy", "monitor", "status"]
)
def test_dry_run_has_no_docker_writes_or_notifications(tmp_path, command, monkeypatch):
    ops, fake = make_ops(tmp_path, command, extra=("--dry-run",))
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("network call")
    )
    with ops.lock(maintenance=True):
        getattr(ops, command)()
    assert fake.commands == []
    assert list(tmp_path.iterdir()) == []


def test_deploy_verifies_backup_before_migrating_and_checks_before_start(tmp_path):
    ops, fake = make_ops(tmp_path, "deploy")
    with ops.lock(maintenance=True):
        ops.deploy()
    commands = fake.commands
    stop = next(index for index, cmd in enumerate(commands) if "stop" in cmd)
    dump = next(index for index, cmd in enumerate(commands) if "pg_dump" in cmd)
    verify = next(index for index, cmd in enumerate(commands) if "pg_restore" in cmd)
    migrate = next(index for index, cmd in enumerate(commands) if cmd[-1] == "migrate")
    check = max(index for index, cmd in enumerate(commands) if "--check" in cmd)
    start = max(index for index, cmd in enumerate(commands) if "up" in cmd)
    assert stop < dump < verify < migrate < check < start
    assert not (ops.state / "maintenance").exists()
    record = json.loads((ops.state / "last-deployment.json").read_text())
    assert record["previous_image"] == "sha256:previousimage"


def test_failed_migration_does_not_start_bot(tmp_path):
    ops, fake = make_ops(tmp_path, "deploy")
    fake.fail = lambda command: command[-1] == "migrate"
    with ops.lock(maintenance=True), pytest.raises(OperationError):
        ops.deploy()
    assert not any("up" in command for command in fake.commands)
    assert not (ops.state / "maintenance").exists()


@pytest.mark.parametrize("failure", ["pg_dump", "pg_restore"])
def test_pre_migration_backup_failure_resumes_exact_previous_container(tmp_path, failure):
    ops, fake = make_ops(tmp_path, "deploy")
    fake.fail = lambda command: failure in command
    with ops.lock(maintenance=True), pytest.raises(OperationError):
        ops.deploy()
    assert fake.commands[-1] == ["docker", "start", "old-bot-container"]
    assert not any(command[-1] == "migrate" for command in fake.commands)
    assert not (ops.state / "maintenance").exists()


def test_boot_start_never_migrates_and_initialize_refuses_existing_tables(tmp_path):
    ops, fake = make_ops(tmp_path, "start")
    ops.start()
    assert not any(command[-1] == "migrate" for command in fake.commands)
    fake.commands.clear()
    fake.table_count = b"10\n"
    with pytest.raises(OperationError, match="empty database"):
        ops.initialize()
    assert not any(command[-1] == "migrate" for command in fake.commands)


def test_restore_requires_explicit_confirmation(tmp_path):
    ops, fake = make_ops(tmp_path)
    ops.args.confirm_restore = None
    with pytest.raises(OperationError, match="confirm-restore"):
        ops.restore()
    assert fake.commands == []


def test_restore_preserves_selected_old_archive_during_retention(tmp_path):
    ops, fake = make_ops(tmp_path)
    archive = ops.backup()
    os.utime(archive, (0, 0))
    ops.args.archive = archive
    ops.args.confirm_restore = "safecheck"
    fake.commands.clear()
    ops.restore()
    assert archive.exists()
    commands = fake.commands
    dump = next(index for index, cmd in enumerate(commands) if "pg_dump" in cmd)
    drop = next(index for index, cmd in enumerate(commands) if "dropdb" in cmd)
    assert dump < drop
    flush = next(index for index, cmd in enumerate(commands) if "FLUSHDB" in cmd[-1])
    final_check = max(index for index, cmd in enumerate(commands) if "--check" in cmd)
    restore = max(index for index, cmd in enumerate(commands) if "pg_restore" in cmd)
    assert restore < flush < final_check
    assert "redis_maintenance_password" in commands[flush][-1]


@pytest.mark.parametrize("failure", ["unhealthy_redis", "maintenance_credentials"])
def test_restore_preflights_redis_before_stopping_or_replacing_database(tmp_path, failure):
    ops, fake = make_ops(tmp_path)
    ops.args.archive = ops.backup()
    ops.args.confirm_restore = "safecheck"
    fake.commands.clear()
    if failure == "unhealthy_redis":
        fake.states["redis"] = "unhealthy"
    else:
        fake.fail = lambda command: "redis" in command and "PING" in command[-1]
    with pytest.raises(OperationError):
        ops.restore()
    assert not any("stop" in command for command in fake.commands)
    assert not any("dropdb" in command for command in fake.commands)
    assert not any("pg_dump" in command for command in fake.commands)


def test_rollback_refuses_incompatible_readiness_before_stopping_bot(tmp_path):
    ops, fake = make_ops(tmp_path)
    ops.args.image = "sha256:" + "a" * 64
    fake.fail = lambda command: "--check" in command
    with pytest.raises(OperationError):
        ops.rollback()
    assert not any("stop" in command for command in fake.commands)


@pytest.mark.parametrize("image", ["safecheck:latest", "safecheck", "registry:5000/safecheck"])
def test_rollback_rejects_floating_or_untagged_images_before_docker(tmp_path, image):
    ops, fake = make_ops(tmp_path)
    ops.args.image = image
    with pytest.raises(OperationError, match="explicit version"):
        ops.rollback()
    assert fake.commands == []


def test_rollback_persists_checked_image_before_stop_and_next_start(tmp_path):
    ops, fake = make_ops(tmp_path)
    ops.args.image = "safecheck:2.12.0"
    ops.args.env_file.write_text(
        "# Preserve paths\nSAFECHECK_IMAGE=safecheck:2.13.0\nOTHER=value\n"
    )
    selected = []

    def runner(command, **kwargs):
        result = fake(command, **kwargs)
        if "stop" in command:
            assert "SAFECHECK_IMAGE=safecheck:2.12.0" in ops.args.env_file.read_text()
        if "config" in command and "--format" in command:
            config = json.loads(result.stdout)
            image = next(
                line.split("=", 1)[1]
                for line in ops.args.env_file.read_text().splitlines()
                if line.startswith("SAFECHECK_IMAGE=")
            )
            selected.append(image)
            config["services"]["bot"]["image"] = image
            config["services"]["migrate"]["image"] = image
            result.stdout = json.dumps(config).encode()
        return result

    ops.runner = runner
    ops.rollback()
    ops.start()
    assert selected == ["safecheck:2.12.0"]
    assert ops.args.env_file.read_text().endswith("OTHER=value\n")
    assert stat.S_IMODE(ops.args.env_file.stat().st_mode) == 0o600


@pytest.mark.parametrize("invalid", ["symlink", "duplicate"])
def test_rollback_refuses_unsafe_persistent_selector_before_stop(tmp_path, invalid):
    ops, fake = make_ops(tmp_path)
    ops.args.image = "safecheck:2.12.0"
    if invalid == "symlink":
        original = tmp_path / "original.env"
        original.write_text("SAFECHECK_IMAGE=safecheck:2.13.0\n")
        ops.args.env_file.symlink_to(original)
    else:
        ops.args.env_file.write_text(
            "SAFECHECK_IMAGE=safecheck:2.13.0\nexport SAFECHECK_IMAGE=safecheck:2.13.0\n"
        )
    with pytest.raises(OperationError):
        ops.rollback()
    assert not any("stop" in command for command in fake.commands)


def test_dry_run_rollback_does_not_create_persistent_selector(tmp_path):
    ops, fake = make_ops(tmp_path, extra=("--dry-run",))
    ops.args.image = "safecheck:2.12.0"
    ops.rollback()
    assert fake.commands == []
    assert list(tmp_path.iterdir()) == []


def test_monitor_reports_unhealthy_without_default_notifications(tmp_path, monkeypatch):
    ops, fake = make_ops(tmp_path, "monitor")
    fake.states["bot"] = "unhealthy"
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("network call")
    )
    assert ops.monitor() is False
    assert json.loads((ops.state / "monitor.json").read_text())["healthy"] is False


def test_monitor_notifies_new_terminal_counts_once_and_stale_outbox(tmp_path, monkeypatch):
    ops, fake = make_ops(tmp_path, "monitor")
    ops.args.alert_token_file = tmp_path / "alert-token"
    ops.args.alert_chat_id = "12345"
    messages = []
    monkeypatch.setattr(ops, "notify", messages.append)
    fake.metrics["ban_terminal"] = 1
    assert ops.monitor() is True
    assert len(messages) == 1
    assert ops.monitor() is True
    assert len(messages) == 1
    fake.metrics["ban_terminal"] = 2
    ops.monitor()
    assert len(messages) == 2
    fake.metrics["oldest_pending_seconds"] = 3601
    assert ops.monitor() is False
    assert len(messages) == 3
    assert any("secret_entrypoint.py" in " ".join(command) for command in fake.commands)


def test_monitor_respects_maintenance_and_detects_stale_marker(tmp_path):
    ops, fake = make_ops(tmp_path, "monitor")
    ops.state.mkdir()
    marker = ops.state / "maintenance"
    marker.write_text(str(time.time()))
    assert ops.monitor() is True
    assert fake.commands == []
    os.utime(marker, (0, 0))
    assert ops.monitor() is False


def test_alert_delivery_uses_mock_transport_and_never_logs_token(tmp_path, monkeypatch):
    ops, _ = make_ops(tmp_path)
    path = tmp_path / "alert-token"
    path.write_text("12345:SECRET_SENTINEL")
    path.chmod(0o600)
    ops.args.alert_token_file = path
    ops.args.alert_chat_id = "12345"
    calls = []

    def transport(request, timeout):
        calls.append((request, timeout))
        raise OSError("SECRET_SENTINEL")

    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(OperationError) as error:
        ops.notify("SAFECheck needs attention")
    assert "SECRET_SENTINEL" not in str(error.value)
    assert len(calls) == 1
    assert json.loads(calls[0][0].data)["chat_id"] == "12345"


def test_secret_entrypoint_preserves_literal_values_and_rejects_nonsecret_keys(
    tmp_path, monkeypatch
):
    path = tmp_path / "runtime.env"
    path.write_text(
        "BOT_TOKEN=12345:TEST\nDATABASE_URL=postgresql+asyncpg://u:${PASS}@postgres/db\nREDIS_URL=redis://redis/0\n"
    )
    for key in ("BOT_TOKEN", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.setenv(key, "")
    load_secrets(path)
    assert os.environ["DATABASE_URL"].endswith("${PASS}@postgres/db")
    path.write_text(path.read_text() + "ADMIN_IDS=12345\n")
    with pytest.raises(ValueError, match="Invalid runtime credential"):
        load_secrets(path)


def test_restore_failed_rescue_backup_restarts_unchanged_bot(tmp_path):
    ops, fake = make_ops(tmp_path)
    ops.args.archive = ops.backup()
    ops.args.confirm_restore = "safecheck"
    fake.commands.clear()
    fake.fail = lambda command: "pg_dump" in command
    with pytest.raises(OperationError):
        ops.restore()
    assert any(command[-2:] == ["start", "bot"] for command in fake.commands)
    assert not any("dropdb" in command for command in fake.commands)
    assert not any("FLUSHDB" in command[-1] for command in fake.commands)


def make_offsite_files(tmp_path):
    archive = tmp_path / "safecheck-20261006T170000Z-deadbeef.dump"
    archive.write_bytes(b"private backup fixture")
    checksum = Path(str(archive) + ".sha256")
    checksum.write_text(digest(archive) + "\n")
    credentials = tmp_path / "rclone.conf"
    credentials.write_text("synthetic credentials; never read by tests or adapter")
    config = tmp_path / "offsite.json"
    config.write_text(
        json.dumps(
            {
                "rclone_binary": "/usr/bin/rclone",
                "rclone_config": str(credentials),
                "remote_prefix": "configured:private backups",
            }
        )
    )
    for path in (archive, checksum, credentials, config):
        path.chmod(0o600)
    return archive, checksum, config, credentials


def test_offsite_adapter_uploads_pair_and_verifies_downloaded_bytes(tmp_path):
    archive, checksum, config, _ = make_offsite_files(tmp_path)
    calls = []

    def rclone(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is False
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] == subprocess.PIPE
        if "check" in command:
            names = Path(command[command.index("--files-from-raw") + 1])
            assert names.read_text().splitlines() == [archive.name, checksum.name]
            assert stat.S_IMODE(names.stat().st_mode) == 0o600
        output = checksum.read_bytes() if "cat" in command else b""
        return subprocess.CompletedProcess(command, 0, output, b"")

    upload_adapter.upload(archive, checksum, config, runner=rclone)
    assert len(calls) == 4
    assert calls[0][-3:] == ["copyto", str(archive), "configured:private backups/" + archive.name]
    assert calls[1][-3:] == ["copyto", str(checksum), "configured:private backups/" + checksum.name]
    assert "--download" in calls[2]
    assert "--one-way" in calls[2]
    assert calls[3][-2:] == ["cat", "configured:private backups/" + checksum.name]


@pytest.mark.parametrize("failure", ["copyto", "check", "cat"])
def test_offsite_transfer_failure_is_generic_and_stops_after_failed_command(tmp_path, failure):
    archive, checksum, config, _ = make_offsite_files(tmp_path)
    calls = []

    def rclone(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, int(failure in command), b"", b"SECRET_SENTINEL"
        )

    with pytest.raises(upload_adapter.UploadError) as error:
        upload_adapter.upload(archive, checksum, config, runner=rclone)
    assert "SECRET_SENTINEL" not in str(error.value)
    assert failure in calls[-1]
    assert archive.exists() and checksum.exists()


def test_offsite_remote_checksum_mismatch_fails_closed(tmp_path):
    archive, checksum, config, _ = make_offsite_files(tmp_path)

    def rclone(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, b"wrong checksum" if "cat" in command else b"", b""
        )

    with pytest.raises(upload_adapter.UploadError, match="Downloaded offsite checksum mismatch"):
        upload_adapter.upload(archive, checksum, config, runner=rclone)


@pytest.mark.parametrize("index", [0, 1, 2, 3])
def test_offsite_adapter_rejects_public_archive_configuration_or_credentials(tmp_path, index):
    files = make_offsite_files(tmp_path)
    files[index].chmod(0o644)
    with pytest.raises(upload_adapter.UploadError, match="owned private"):
        upload_adapter.upload(
            *files[:3], runner=lambda *args, **kwargs: pytest.fail("external call")
        )


def test_offsite_adapter_rejects_corrupt_local_archive_before_external_call(tmp_path):
    archive, checksum, config, _ = make_offsite_files(tmp_path)
    archive.write_bytes(b"corrupted")
    with pytest.raises(upload_adapter.UploadError, match="Local archive checksum mismatch"):
        upload_adapter.upload(
            archive, checksum, config, runner=lambda *args, **kwargs: pytest.fail("external call")
        )


def test_offsite_adapter_rejects_unconfigured_destination_before_external_call(tmp_path):
    archive, checksum, config, _ = make_offsite_files(tmp_path)
    values = json.loads(config.read_text())
    values["remote_prefix"] = "REPLACE_WITH_CONFIGURED_REMOTE:REPLACE_WITH_PREFIX"
    config.write_text(json.dumps(values))
    with pytest.raises(upload_adapter.UploadError, match="explicit rclone remote"):
        upload_adapter.upload(
            archive, checksum, config, runner=lambda *args, **kwargs: pytest.fail("external call")
        )

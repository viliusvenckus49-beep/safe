"""Switch the existing user session to lookup-only mode; never restore staff sending."""

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

PHASE = "preflight"


def imported(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def atomic(path, data):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError("Untrusted settings path")
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".lookup-only-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchown(handle.fileno(), info.st_uid, info.st_gid)
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


PROBE = r"""
import asyncio,json
from app.config import Settings
from app.db import create_database
from app.mtproto_relay import GroupHelpRelay,_UsernameOnlyClient
from telethon.tl.functions.messages import SendMessageRequest
from telethon.tl.types import InputPeerSelf
async def main():
 cfg=Settings();assert not cfg.group_help_enabled and cfg.moderation_mtproto_enabled
 engine,sessions=create_database(cfg.database_url)
 relay=None
 try:
  relay=await GroupHelpRelay.connect_identity(cfg,sessions)
  assert relay.staff is None and isinstance(relay.client,_UsernameOnlyClient)
  try:await relay.client(SendMessageRequest(peer=InputPeerSelf(),message='TRANSPORT_GUARD_TEST',random_id=1))
  except ValueError:blocked=True
  else:raise RuntimeError('Write guard missing')
  result=await relay.lookup_identity('CrimsonPrimeBot',refresh=True)
  assert result.user is not None and result.user.id==9002888138
  print(json.dumps({'staff_sending':False,'lookup_enabled':True,'write_request_blocked':blocked,'username_lookup_verified':True,'bot_id':result.user.id}))
 finally:
  if relay:await relay.close()
  await engine.dispose()
asyncio.run(main())
"""


def main():
    global PHASE
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    rows = json.loads(
        subprocess.check_output(
            docker + ["inspect", "safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"],
            stderr=subprocess.DEVNULL,
        )
    )
    bot = rows[0]
    root = Path(bot["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    helper = imported(root / "deploy/prepare_identity_change.py", "identity_prepare")
    ops = imported(root / "deploy/ops.py", "runtime_ops")
    settings, credentials = helper.configuration_paths(Path("/etc/safecheck/compose.env"))
    helper.checked_file(settings)
    before = settings.read_bytes()
    credentials_hash = ops.digest(credentials)
    after = helper.changed(
        helper.changed(before.decode(), "GROUP_HELP_ENABLED", "false"),
        "MODERATION_MTPROTO_ENABLED",
        "true",
    ).encode()
    assert helper.scalar(before.decode(), "ADMIN_IDS") == helper.scalar(after.decode(), "ADMIN_IDS")
    assert (
        helper.scalar(before.decode(), "GROUP_OWNER_ID")
        == helper.scalar(after.decode(), "GROUP_OWNER_ID")
        == "8896102663"
    )
    for row in rows:
        assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
    args = SimpleNamespace(
        project="safecheck",
        env_file=Path("/etc/safecheck/compose.env"),
        root=root,
        backup_dir=Path("/var/backups/safecheck"),
        state_dir=Path("/var/lib/safecheck"),
        dry_run=False,
    )
    operation = ops.Operations(args)
    backup = Path("/var/lib/safecheck/staff-transport-disabled") / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    ops.private_directory(backup)
    ops.private_write(backup / "runtime.before", before.decode())
    ops.private_write(backup / "runtime.next", after.decode())
    with operation.lock(maintenance=True):
        assert settings.read_bytes() == before
        PHASE = "stop-staff-sender"
        operation.dc("stop", "--timeout", "45", "bot")
        PHASE = "set-lookup-only"
        atomic(settings, after)
        assert ops.digest(credentials) == credentials_hash
        PHASE = "validate-configuration"
        operation.dc("run", "--rm", "-T", "--no-deps", "bot", "python", "-m", "app.main", "--check")
        PHASE = "verify-read-only-session"
        result = operation.dc(
            "run", "--rm", "-T", "--no-deps", "bot", "python", "-c", PROBE
        ).decode()
        safe = json.loads(next(x for x in reversed(result.splitlines()) if x.startswith("{")))
        print("LookupOnlySession=" + json.dumps(safe, sort_keys=True), flush=True)
        PHASE = "start-native-bot"
        operation.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")
        current = json.loads(
            operation.run(
                docker + ["inspect", "safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"]
            )
        )
        assert current[0]["Config"]["Image"] == bot["Config"]["Image"]
        assert all(a["Id"] == b["Id"] for a, b in zip(rows[1:], current[1:], strict=True))
        assert ops.digest(credentials) == credentials_hash and settings.read_bytes() == after
        for row in current:
            assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
        print(
            "StaffTransport=DISABLED; IdentityLookup=ENABLED; MainBot=HEALTHY; DatabaseAndRedis=UNCHANGED; Credentials=UNCHANGED",
            flush=True,
        )
        ops.private_write(
            backup / "result.json",
            json.dumps({"staff_sending": False, "lookup_enabled": True, "healthy": True}),
        )


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        print(
            "StaffTransportSwitch=FAILED; Phase="
            + PHASE
            + "; Type="
            + type(error).__name__
            + "; staff sending will not be automatically re-enabled",
            flush=True,
        )
        raise SystemExit(1) from None

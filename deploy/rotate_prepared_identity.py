"""Guarded identity-only cutover: tested image, verified backups, no DB migration or reset."""

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

IDENTITY = r"""
import asyncio,json,sys
from aiogram import Bot
from app.bot.session import telegram_session
from app.config import Settings
async def main():
 expected=json.load(sys.stdin); cfg=Settings()
 assert cfg.group_owner==expected['owner'] and cfg.admin_ids==expected['admins']
 async with Bot(cfg.bot_token.get_secret_value(),session=telegram_session(cfg)) as bot:
  me=await bot.get_me()
 assert me.id==expected['bot_id'] and me.username.lower()==expected['username'].lower()
 print(json.dumps({'bot_id':me.id,'username':me.username,'owner':cfg.group_owner,'admin_ids_unchanged':True}))
try:asyncio.run(main())
except Exception:print('Identity verification failed',file=sys.stderr);sys.exit(1)
"""


def imported(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def atomic_replace(path, data):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Active config must be a regular file")
    temporary = path.with_name("." + path.name + ".identity-" + uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(data)
            os.fchown(file.fileno(), info.st_uid, info.st_gid)
            os.fchmod(file.fileno(), stat.S_IMODE(info.st_mode))
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def private(path, directory=False):
    info = path.lstat()
    valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise RuntimeError("Untrusted private identity stage")


def staged(root, stage):
    helper = imported(root / "deploy/prepare_identity_change.py", "identity_preparer")
    private(stage, True)
    private(stage / "manifest.json")
    manifest = json.loads((stage / "manifest.json").read_text())
    import hashlib

    data = {}
    for name in ("settings.before", "credentials.before", "settings.next", "credentials.next"):
        file = stage / name
        private(file)
        data[name] = file.read_bytes()
        if hashlib.sha256(data[name]).hexdigest() != manifest["sha256"][name]:
            raise RuntimeError("Staged identity content changed")
    paths = helper.configuration_paths(Path("/etc/safecheck/compose.env"))
    if paths != (Path(manifest["settings_path"]), Path(manifest["credentials_path"])):
        raise RuntimeError("Active identity paths changed")
    owner = helper.positive_id(str(manifest["new_owner"]))
    token = helper.scalar(data["credentials.next"].decode(), "BOT_TOKEN")
    admins = helper.scalar(data["settings.before"].decode(), "ADMIN_IDS")
    if (
        data["settings.next"].decode()
        != helper.changed(data["settings.before"].decode(), "GROUP_OWNER_ID", str(owner))
        or data["credentials.next"].decode()
        != helper.changed(data["credentials.before"].decode(), "BOT_TOKEN", token)
        or int(token.split(":", 1)[0]) != manifest["identity"]["bot_id"]
        or manifest["identity"]["webhook_present"]
    ):
        raise RuntimeError("Unexpected identity replacement")
    return paths, data, {**manifest["identity"], "owner": owner, "admins": admins}


def originals(paths, data):
    if (
        paths[0].read_bytes() != data["settings.before"]
        or paths[1].read_bytes() != data["credentials.before"]
    ):
        raise RuntimeError("Active configuration changed after identity preparation")


def payload_call(release, command, value):
    with tempfile.TemporaryFile() as file:
        file.write(json.dumps(value).encode())
        file.seek(0)
        return json.loads(release.run(command, stdin=file))


def verify_identity(release, expected):
    result = payload_call(
        release,
        release.candidate.compose
        + ["run", "--rm", "-T", "--no-deps", "bot", "python", "-c", IDENTITY],
        expected,
    )
    print("RotatedIdentity=" + json.dumps(result, sort_keys=True), flush=True)


def verify_retention(release, baseline):
    result = payload_call(
        release,
        release.candidate.compose
        + [
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--volume",
            str(release.root / "deploy/production_probe.py") + ":/run/probe.py:ro",
            "bot",
            "python",
            "/run/probe.py",
            "original",
        ],
        baseline,
    )
    print("IdentityRetention=" + json.dumps(result, sort_keys=True), flush=True)


def activate(
    release, paths, data, expected, identity_check=verify_identity, retention_check=verify_retention
):
    originals(paths, data)
    release.ops.private_directory(release.state)
    release.ops.private_directory(release.backup_dir)
    release.idle()
    release.writers()
    release.preserve_services()
    if shutil.disk_usage(release.root).free < 2 * 1024**3:
        raise RuntimeError("Insufficient identity release space")
    release.run(["docker", "build", "-t", release.image, str(release.root)])
    release.rehearsal(release.backup("identity-online"))
    with release.original.lock(maintenance=True):
        originals(paths, data)
        if release.inspect("safecheck-bot-1")["Id"] != release.old["Id"]:
            raise RuntimeError("Original bot changed")
        release.idle()
        release.writers()
        release.preserve_services()
        release.original.dc("stop", "--timeout", "45", "bot")
        changed = False
        starting = False
        try:
            if release.inspect("safecheck-bot-1")["State"]["Running"]:
                raise RuntimeError("Original bot did not stop")
            release.idle()
            archive = release.backup("identity-frozen")
            baseline = release.rehearsal(archive)
            changed = True
            atomic_replace(paths[0], data["settings.next"])
            atomic_replace(paths[1], data["credentials.next"])
            # Paths, all data services and their credentials remain unchanged.
            # Existing release preparation reuses the prebuilt exact-source image.
            release.prepare()
            identity_check(release, expected)
            release.candidate.readiness()
            retention_check(release, baseline)
            starting = True
            release.candidate.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")
            release.preserve_services()
            release.original.persist_image(release.image)
            release.ops.private_write(
                release.state / "identity-release.json",
                json.dumps(
                    {
                        "sha": release.sha,
                        "status": "HEALTHY",
                        "backup": str(archive),
                        "bot_id": expected["bot_id"],
                        "username": expected["username"],
                        "owner": expected["owner"],
                        "admin_ids_unchanged": True,
                    },
                    sort_keys=True,
                ),
            )
        except BaseException:
            if starting:
                release.candidate.dc("stop", "--timeout", "45", "bot")
            if changed:
                atomic_replace(paths[0], data["settings.before"])
                atomic_replace(paths[1], data["credentials.before"])
            release.original.persist_image(release.old["Config"]["Image"])
            release.original.dc("up", "-d", "--no-deps", "--wait", "--wait-timeout", "240", "bot")
            print("IdentityRollback=CONFIG_AND_IMAGE_ONLY; DATABASE_AND_REDIS_RETAINED", flush=True)
            raise
    print("IDENTITY_RELEASE=HEALTHY; Schema=0025; Image=" + release.image, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("sha")
    parser.add_argument("stage_id")
    args = parser.parse_args()
    if (
        os.geteuid() != 0
        or not re.fullmatch(r"[a-f0-9]{40}", args.sha)
        or not re.fullmatch(r"[a-f0-9]{32}", args.stage_id)
    ):
        raise RuntimeError("Use a verified root deployment and explicit prepared stage")
    stage = Path("/var/lib/safecheck/identity-change") / args.stage_id
    paths, data, expected = staged(args.root, stage)
    originals(paths, data)
    release = imported(args.root / "deploy/production_release.py", "identity_release").Release(
        args.root, args.sha
    )
    if "0025" not in release.sql("SELECT version_num FROM alembic_version"):
        raise RuntimeError("Identity cutover must not migrate schema")
    # The installed relay scope still references this withdrawn group. It is
    # independently confirmed unavailable to the new bot and disabled in DB.
    stale = -1003802477695
    helper = imported(args.root / "deploy/prepare_identity_change.py", "identity_scope_reader")
    next_settings = data["settings.next"].decode()
    value = helper.scalar(next_settings, "GROUP_HELP_SCOPE_IDS", optional=True)
    if value:
        scope = {int(x.strip()) for x in value.split(",") if x.strip()}
        if stale in scope:
            state = release.sql("SELECT enabled FROM managed_groups WHERE chat_id=" + str(stale))
            if "f" not in state:
                raise RuntimeError("Stale relay group state changed; review scope")
            scope.remove(stale)
            if not scope:
                raise RuntimeError("Relay scope must remain nonempty")
            replacement = ",".join(str(x) for x in sorted(scope))
            expression = r"^([ \t]*(?:export[ \t]+)?GROUP_HELP_SCOPE_IDS[ \t]*=)[^\r\n]*"
            next_settings, count = re.subn(
                expression, lambda m: m[1] + replacement, next_settings, flags=re.M
            )
            if count != 1:
                raise RuntimeError("Ambiguous relay scope setting")
            data["settings.next"] = next_settings.encode()
            print(
                "RelayScopeReconciled=REMOVED_DISABLED_GROUP; Remaining=" + str(len(scope)),
                flush=True,
            )
    activate(release, paths, data, expected)


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        print(
            "IDENTITY_RELEASE=STOPPED; Type="
            + type(error).__name__
            + "; raw output and credentials withheld",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

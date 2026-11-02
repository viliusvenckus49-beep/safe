#!/usr/bin/env python3
"""Prepare a private replacement identity on the VPS without changing the running bot."""

import argparse
import getpass
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import warnings
from pathlib import Path
from uuid import uuid4

DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]
PROBE = r"""
import asyncio,json,sys
from aiogram import Bot
from app.bot.session import telegram_session
from app.config import Settings
async def main():
    token=json.load(sys.stdin)['token']
    # The installed old image may still require OWNER to belong to ADMIN_IDS.
    # This read-only token probe never starts polling, a relay or any worker.
    cfg=Settings(bot_token=token,group_owner_id=None,
                 group_help_enabled=False,moderation_mtproto_enabled=False)
    async with Bot(token,session=telegram_session(cfg)) as bot:
        me=await bot.get_me()
        hook=await bot.get_webhook_info()
        return {'bot_id':me.id,'username':me.username,'webhook_present':bool(hook.url)}
try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),30))))
except BaseException:print('IdentityProbe=FAILED',file=sys.stderr);sys.exit(1)
"""
COUNTS = """BEGIN READ ONLY;
SELECT json_build_object(
 'schema',(SELECT version_num FROM alembic_version),
 'groups',(SELECT count(*) FROM managed_groups),
 'active_ads',(SELECT count(*) FROM ad_schedules WHERE status='ACTIVE'),
 'media_versions',(SELECT count(*) FROM ad_content_versions
   WHERE CASE WHEN json_typeof(content->'media')='array'
     THEN json_array_length(content->'media') ELSE 0 END>0),
 'report_media',(SELECT count(*) FROM report_evidence),
 'pending_sanctions',(SELECT count(*) FROM group_sanction_jobs
   WHERE status IN ('PENDING','PROCESSING','UNCERTAIN')),
 'pending_checks',(SELECT count(*) FROM group_check_events
   WHERE state IN ('PENDING','WAITING','SENDING','QUEUED')),
 'pending_ads',(SELECT count(*) FROM ad_publication_attempts
   WHERE status IN ('PENDING','CLAIMED','SENDING','DEFERRED','UNCERTAIN'))
); COMMIT;"""


class PreparationError(Exception):
    """Static errors only; never disclose command output or configuration values."""


def run(args, *, data=None, env=None):
    selected = dict(os.environ if env is None else env)
    for key in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
    ):
        selected.pop(key, None)
    result = subprocess.run(
        args,
        input=data,
        capture_output=True,
        env=selected,
        timeout=90,
        check=False,
    )
    if result.returncode:
        raise PreparationError("Komanda nepavyko; aktyvūs nustatymai nepakeisti.")
    return result.stdout


def scalar(text, key, *, optional=False):
    matches = re.findall(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=([^\r\n]*)", text, re.M)
    if not matches and optional:
        return None
    if len(matches) != 1:
        raise PreparationError("Trūksta nustatymo arba jis įrašytas kelis kartus.")
    parts = shlex.split(matches[0], comments=True)
    if len(parts) != 1:
        raise PreparationError("Nepalaikomas nustatymo formatas.")
    return parts[0]


def changed(text, key, value):
    if not re.fullmatch(r"[A-Za-z0-9_:\-]+", value):
        raise PreparationError("Netinkama nauja reikšmė.")
    pattern = r"^(\s*(?:export\s+)?" + re.escape(key) + r"\s*=)[^\r\n]*"
    matches = list(re.finditer(pattern, text, re.M))
    if len(matches) > 1:
        raise PreparationError("Pasikartojantis nustatymas.")
    if not matches:
        return text + ("" if text.endswith("\n") else "\n") + key + "=" + value + "\n"
    return re.sub(pattern, lambda m: m[1] + value, text, flags=re.M)


def read_regular(path, *, secret=False):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (secret and info.st_mode & 0o077):
        raise PreparationError("Nesaugus arba netinkamas konfigūracijos failas.")
    return path.read_bytes().decode("utf-8")


def positive_id(value):
    if not re.fullmatch(r"[0-9]{1,19}", value) or not 0 < int(value) <= 9223372036854775807:
        raise PreparationError("Reikia teigiamo skaitinio Telegram ID.")
    return int(value)


def prepare(compose_env, state_dir, token, owner, username):
    positive_id(str(owner))
    username = username.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username):
        raise PreparationError("Netinkamas naujo boto username.")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", token):
        raise PreparationError("Netinkamas boto tokeno formatas.")
    row = json.loads(run(DOCKER + ["inspect", "safecheck-bot-1"]))[0]
    labels = row.get("Config", {}).get("Labels", {})
    if labels.get("com.docker.compose.project") != "safecheck":
        raise PreparationError("Neatpažintas pagrindinis boto konteineris.")
    root = Path(labels.get("com.docker.compose.project.working_dir", ""))
    if not root.is_absolute() or not (root / "docker-compose.production.yml").is_file():
        raise PreparationError("Nerastas esamas boto projektas.")
    selectors = read_regular(compose_env)
    settings_path = Path(scalar(selectors, "SAFECHECK_ENV_FILE"))
    secrets_path = Path(scalar(selectors, "SAFECHECK_SECRETS_DIR")) / "runtime.env"
    if not settings_path.is_absolute() or not secrets_path.is_absolute():
        raise PreparationError("Reikia absoliučių esamų konfigūracijų kelių.")
    settings = read_regular(settings_path)
    secrets = read_regular(secrets_path, secret=True)
    admins = scalar(settings, "ADMIN_IDS")
    old_token = scalar(secrets, "BOT_TOKEN")
    if old_token.split(":", 1)[0] == token.split(":", 1)[0]:
        raise PreparationError("Tai ta pati boto tapatybė; reikalingas naujas botas.")
    dc = DOCKER + [
        "compose",
        "--project-name",
        "safecheck",
        "--env-file",
        str(compose_env),
        "-f",
        str(root / "docker-compose.production.yml"),
    ]
    env = {
        **os.environ,
        "SAFECHECK_IMAGE": row["Image"],
        "SAFECHECK_ENV_FILE": str(settings_path),
        "SAFECHECK_SECRETS_DIR": str(secrets_path.parent),
    }
    identity = json.loads(
        run(
            dc + ["run", "--rm", "--no-deps", "-T", "bot", "python", "-c", PROBE],
            data=json.dumps({"token": token}).encode(),
            env=env,
        )
    )
    if (
        identity["bot_id"] != int(token.split(":", 1)[0])
        or identity["username"].lower() != username.lower()
    ):
        raise PreparationError("Tokenas neatitinka nurodyto naujo boto.")
    output = run(
        DOCKER
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
            COUNTS,
        ]
    ).decode()
    counts = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    replacement_settings = changed(settings, "GROUP_OWNER_ID", str(owner))
    replacement_secrets = changed(secrets, "BOT_TOKEN", token)
    if scalar(replacement_settings, "ADMIN_IDS") != admins:
        raise PreparationError("ADMIN_IDS išsaugojimo patikra nepraėjo.")
    # Stage only. No DB changes, service stop/restart, migration or active file replacement.
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state_dir.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise PreparationError("Nesaugus paruošimo katalogas.")
    target = state_dir / uuid4().hex
    target.mkdir(mode=0o700)
    files = {
        "settings.before": settings,
        "credentials.before": secrets,
        "settings.next": replacement_settings,
        "credentials.next": replacement_secrets,
    }
    for name, content in files.items():
        with open(
            target / name,
            "x",
            encoding="utf-8",
            newline="",
            opener=lambda p, f: os.open(p, f, 0o600),
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    manifest = {
        "active_files_changed": False,
        "admin_ids_unchanged": True,
        "old_owner": scalar(settings, "GROUP_OWNER_ID", optional=True),
        "new_owner": owner,
        "identity": identity,
        "database_counts": counts,
        "current_root": str(root),
        "current_image": row["Image"],
        "settings_path": str(settings_path),
        "credentials_path": str(secrets_path),
        "mtproto_enabled": any(
            (scalar(settings, field, optional=True) or "").lower()
            in ("true", "1", "yes", "on", "t", "y")
            for field in ("GROUP_HELP_ENABLED", "MODERATION_MTPROTO_ENABLED")
        ),
        "sha256": {
            name: hashlib.sha256((target / name).read_bytes()).hexdigest() for name in files
        },
    }
    with open(target / "manifest.json", "x", opener=lambda p, f: os.open(p, f, 0o600)) as handle:
        json.dump(manifest, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return target, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-env", type=Path, default=Path("/etc/safecheck/compose.env"))
    parser.add_argument(
        "--state-dir", type=Path, default=Path("/var/lib/safecheck/identity-change")
    )
    args = parser.parse_args()
    if os.geteuid() != 0 or not sys.stdin.isatty():
        raise PreparationError("Paleisk serveryje su sudo ir interaktyviu terminalu.")
    username = input("Naujo boto @username: ").strip()
    owner = positive_id(input("Naujo savininko Telegram ID: ").strip())
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass("Naujo boto tokenas (nerodomas): ").strip()
    target, report = prepare(args.compose_env, args.state_dir, token, owner, username)
    print("IDENTITY_PREPARED=" + str(target))
    print("Bot=@" + report["identity"]["username"] + "; Owner=" + str(owner))
    print("ADMIN_IDS=UNCHANGED; ACTIVE_CONFIG=UNCHANGED; DATABASE=READ_ONLY")
    print("ReviewCounts=" + json.dumps(report["database_counts"], sort_keys=True))
    print(
        "MTProtoReview="
        + str(report["mtproto_enabled"])
        + "; WebhookPresent="
        + str(report["identity"]["webhook_present"])
    )
    print("Paruošta. Botas dar nepakeistas; tokeno ar paruoštų failų turinio niekam nesiųsk.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("Paruošimas nutrauktas; aktyvūs nustatymai nepakeisti.", file=sys.stderr)
        raise SystemExit(1) from None
    except PreparationError as error:
        print("IDENTITY_PREPARE=FAILED; " + str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print(
            "IDENTITY_PREPARE=FAILED; aktyvūs nustatymai nepakeisti. Slaptos reikšmės nerodomos.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None

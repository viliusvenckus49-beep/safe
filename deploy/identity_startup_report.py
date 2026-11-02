"""Report only service status and safe startup diagnostics, without credentials."""

import json
import subprocess
from pathlib import Path

STAFF = r"""
import asyncio,json,sqlite3,tempfile
from pathlib import Path
from app.config import Settings
from app.mtproto_relay import private_configuration
from telethon import TelegramClient
async def main():
 cfg=Settings();result={'configured_owner':cfg.group_owner,'staff_id':cfg.group_help_staff_id,'staff_bot_pin':cfg.group_help_bot_id,'scope':sorted(cfg.group_help_scope)}
 directory=Path(cfg.group_help_state_dir);secret=private_configuration(directory)
 with tempfile.TemporaryDirectory(prefix='staff-review-') as temp:
  source=sqlite3.connect((directory/'account.session').as_uri()+'?mode=ro',uri=True);target=sqlite3.connect(Path(temp)/'account.session');source.backup(target);source.close();target.close()
  client=TelegramClient(str(Path(temp)/'account.session'),secret['api_id'],secret['api_hash'],flood_sleep_threshold=0,request_retries=0,connection_retries=1,timeout=5)
  try:
   await asyncio.wait_for(client.connect(),12)
   me=await client.get_me();result['account_id']=getattr(me,'id',None)
   staff=await client.get_entity(cfg.group_help_staff_id);result['staff_left']=bool(getattr(staff,'left',False))
   vendor=await client.get_entity('ghStaffBot');result['vendor_id']=vendor.id;result['vendor_pin_matches']=vendor.id==cfg.group_help_bot_id
   users=await client.get_participants(staff);result['vendor_in_staff']=any(u.id==vendor.id for u in users)
  except Exception as error:result['staff_error']=type(error).__name__
  finally:await client.disconnect()
 return result
try: print(json.dumps(asyncio.run(asyncio.wait_for(main(),40))))
except Exception as error:print(json.dumps({'error_type':type(error).__name__}))
"""


def main():
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    row = json.loads(
        subprocess.check_output(docker + ["inspect", "safecheck-bot-1"], stderr=subprocess.DEVNULL)
    )[0]
    print(
        "IdentityStartupState="
        + json.dumps(
            {
                "image": row["Config"]["Image"],
                "running": row["State"]["Running"],
                "health": row["State"].get("Health", {}).get("Status"),
                "restart_count": row["RestartCount"],
            }
        ),
        flush=True,
    )
    result = subprocess.run(
        docker + ["logs", "--tail", "100", "safecheck-bot-1"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in (result.stdout + result.stderr).splitlines():
        try:
            value = json.loads(line)
            keys = ("event", "exception_type", "operation")
            safe = {k: value[k] for k in keys if k in value}
            if safe:
                print("IdentityStartupEvent=" + json.dumps(safe), flush=True)
        except (ValueError, TypeError):
            if line.startswith("SAFECheck startup") or line.startswith("SAFECheck configuration"):
                print("IdentityStartupText=" + line, flush=True)
    query = "BEGIN READ ONLY; SELECT json_build_object('chat_id',g.chat_id,'approved',g.approved,'enabled',g.enabled,'restrict',g.can_restrict_members,'owner_removed',EXISTS(SELECT 1 FROM audit_events a WHERE a.action='group_removed' AND a.details->>'chat_id'=g.chat_id::text)) FROM managed_groups g ORDER BY g.chat_id; COMMIT;"
    data = subprocess.check_output(
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
            "-Atc",
            query,
        ],
        stderr=subprocess.DEVNULL,
    ).decode()
    print(
        "IdentityRegisteredState="
        + json.dumps([json.loads(x) for x in data.splitlines() if x.startswith("{")]),
        flush=True,
    )
    labels = row["Config"]["Labels"]
    root = Path(labels["com.docker.compose.project.working_dir"])
    run = docker + [
        "compose",
        "--project-name",
        "safecheck",
        "--env-file",
        "/etc/safecheck/compose.env",
        "-f",
        str(root / "docker-compose.production.yml"),
        "run",
        "--rm",
        "-T",
        "--no-deps",
        "bot",
        "python",
        "-c",
        STAFF,
    ]
    reply = subprocess.run(run, capture_output=True, text=True, timeout=70, check=False)
    if reply.returncode == 0:
        safe = json.loads(next(x for x in reversed(reply.stdout.splitlines()) if x.startswith("{")))
        print("IdentityStaffReadiness=" + json.dumps(safe, sort_keys=True), flush=True)
    # File metadata only; no session/API credential contents.
    for mount in row.get("Mounts", []):
        if mount["Destination"] == "/run/mtproto":
            import stat

            root = Path(mount["Source"])
            for file in (root, root / "api.json", root / "account.session"):
                info = file.lstat()
                print(
                    "IdentitySessionFile="
                    + json.dumps(
                        {
                            "path": str(file),
                            "uid": info.st_uid,
                            "gid": info.st_gid,
                            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("IdentityStartupReport=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

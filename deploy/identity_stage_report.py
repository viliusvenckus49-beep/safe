"""Inspect private prepared identity metadata and retained work; never disclose credentials."""

import hashlib
import json
import re
import stat
import types
from pathlib import Path

HELPER_SHA = "3f1056df87dc58cad9fe7413d0cf6c8499d3b375fd1d74aeadca9fbbf454b462"


PROBE = r"""
import asyncio,json,sqlite3,tempfile
from pathlib import Path
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest,TelegramForbiddenError
from app.config import Settings
from app.bot.session import telegram_session
async def main():
 p=json.load(__import__('sys').stdin)
 cfg=Settings(bot_token=p['token'],group_owner_id=None,group_help_enabled=False,moderation_mtproto_enabled=False)
 result={}
 async with Bot(p['token'],session=telegram_session(cfg)) as bot:
  me=await bot.get_me()
  assert me.id==p['bot_id'] and me.username.lower()==p['username'].lower()
  result['bot']={'id':me.id,'username':me.username}
  try:
   updates=await bot.get_updates(limit=100,timeout=0)
   pending=sorted({u.my_chat_member.chat.id for u in updates if u.my_chat_member and u.my_chat_member.chat.type in ('group','supergroup')})
  except Exception as error:
   pending=[];result['update_probe_error']=type(error).__name__
  result['unacknowledged_group_events']=pending
  groups=[]
  for cid in sorted(set(p['groups'])|set(pending)):
   try:
    member=await bot.get_chat_member(cid,me.id)
    groups.append({'chat_id':cid,'status':str(member.status),'restrict':bool(getattr(member,'can_restrict_members',False))})
   except (TelegramBadRequest,TelegramForbiddenError):groups.append({'chat_id':cid,'status':'NO_ACCESS','restrict':False})
  result['groups']=groups
  try:
   owner=await bot.get_chat(p['owner'])
   result['owner_chat_known']=owner.id==p['owner']
  except (TelegramBadRequest,TelegramForbiddenError):result['owner_chat_known']=False
 if p['mtproto']:
  from app.mtproto_relay import private_configuration
  from telethon import TelegramClient
  try:
   directory=Path(cfg.group_help_state_dir);secret=private_configuration(directory)
   with tempfile.TemporaryDirectory(prefix='identity-review-') as temp:
    copy=Path(temp)/'account.session'
    source=sqlite3.connect((directory/'account.session').as_uri()+'?mode=ro',uri=True)
    target=sqlite3.connect(copy);source.backup(target);target.close();source.close()
    client=TelegramClient(str(copy),secret['api_id'],secret['api_hash'],flood_sleep_threshold=0,request_retries=0,connection_retries=1,timeout=5)
    try:
     await asyncio.wait_for(client.connect(),12)
     account=await asyncio.wait_for(client.get_me(),12)
     result['relay']={'authorized':bool(account and not getattr(account,'deleted',False)),'account_id':getattr(account,'id',None)}
    finally:await client.disconnect()
  except Exception as error:result['relay']={'authorized':False,'error_type':type(error).__name__}
 return result
try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),55))))
except Exception as error:print(json.dumps({'probe_error':type(error).__name__}))
"""


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
    due = (
        helper.run(
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
                "BEGIN READ ONLY; SELECT count(*) FROM ban_actions WHERE status IN ('PENDING','PROCESSING'); COMMIT;",
            ]
        )
        .decode()
        .splitlines()
    )
    print("IdentityBanOutbox=" + json.dumps([x for x in due if x.isdigit()]), flush=True)
    if len(stages) == 1:
        stage = stages[0]
        token = helper.scalar((Path(stage["path"]) / "credentials.next").read_text(), "BOT_TOKEN")
        manifest = json.loads((Path(stage["path"]) / "manifest.json").read_text())
        group_ids = (
            helper.run(
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
                    "BEGIN READ ONLY; SELECT chat_id FROM managed_groups ORDER BY chat_id; COMMIT;",
                ]
            )
            .decode()
            .splitlines()
        )
        print("IdentityOldOwner=" + json.dumps(manifest.get("old_owner")), flush=True)
        root = Path(manifest["current_root"])
        selectors = Path("/etc/safecheck/compose.env")
        dc = docker + [
            "compose",
            "--project-name",
            "safecheck",
            "--env-file",
            str(selectors),
            "-f",
            str(root / "docker-compose.production.yml"),
        ]
        data = {
            "token": token,
            "bot_id": stage["identity"]["bot_id"],
            "username": stage["identity"]["username"],
            "owner": stage["new_owner"],
            "mtproto": stage["mtproto_enabled"],
            "groups": [int(cid) for cid in group_ids if cid.startswith("-")],
        }
        raw_result = helper.run(
            dc + ["run", "--rm", "-T", "--no-deps", "bot", "python", "-c", PROBE],
            data=json.dumps(data).encode(),
        )
        result = json.loads(
            next(
                line for line in reversed(raw_result.decode().splitlines()) if line.startswith("{")
            )
        )
        print("IdentityLiveReads=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("IdentityStageReport=FAILED; credential contents withheld", flush=True)
        raise SystemExit(1) from None

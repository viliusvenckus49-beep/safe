"""Verify the full public identity path without messaging or sharing the live session DB."""

import json
import subprocess
import sys
from pathlib import Path

PROBE = r"""
import asyncio,json,os,sqlite3,tempfile
from pathlib import Path
from aiogram import Bot
from sqlalchemy import text
from app.bot.session import telegram_session
from app.config import Settings
from app.db import create_database
from app.mtproto_relay import GroupHelpRelay,_UsernameOnlyClient,private_configuration,set_relay
from app.services import Service
from telethon.tl.functions.messages import SendMessageRequest
from telethon.tl.types import InputPeerSelf

async def main():
 cfg=Settings()
 assert not cfg.group_help_enabled and cfg.moderation_mtproto_enabled
 assert cfg.username_lookup_enabled
 original=Path(cfg.group_help_state_dir)
 private_configuration(original)
 engine,sessions=create_database(cfg.database_url)
 try:
  async with Bot(cfg.bot_token.get_secret_value(),session=telegram_session(cfg)) as bot:
   me=await bot.get_me()
  assert me.id==9002888138 and me.username.lower()=='crimsonprimebot'
  with tempfile.TemporaryDirectory(prefix='lookup-verify-') as name:
   copy=Path(name)
   for source in (original/'api.json',):
    fd=os.open(copy/source.name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as output:output.write(source.read_bytes())
   fd=os.open(copy/'account.session',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
   source=sqlite3.connect((original/'account.session').as_uri()+'?mode=ro',uri=True)
   target=sqlite3.connect(copy/'account.session')
   try:source.backup(target)
   finally:target.close();source.close()
   lookup=cfg.model_copy(update={'group_help_state_dir':str(copy)})
   relay=await GroupHelpRelay.connect_identity(lookup,sessions)
   try:
    set_relay(relay)
    assert relay.staff is None and isinstance(relay.client,_UsernameOnlyClient)
    try:await relay.client(SendMessageRequest(peer=InputPeerSelf(),message='BLOCKED_WRITE_PROBE',random_id=1))
    except ValueError:blocked=True
    else:raise RuntimeError('Sending was not blocked')
    async with sessions() as session:
     service=Service(lookup,session)
     result=await service.profile('@'+me.username,refresh_identity=True)
     assert result['identity_lookup'].code=='resolved'
     assert result['user'].telegram_id==me.id
     pinned=await service.resolve('u:'+str(result['user'].id))
     assert pinned.telegram_id==me.id
     assert await session.scalar(text('SELECT version_num FROM alembic_version'))=='0025'
    return {'bot_id':me.id,'username':me.username,'public_lookup':'resolved','pinned_identity_verified':True,'staff_sending':False,'write_request_blocked':blocked,'schema':'0025'}
   finally:set_relay(None);await relay.close()
 finally:await engine.dispose()

try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),70)),sort_keys=True))
except Exception as error:
 print(json.dumps({'verification_error':type(error).__name__}))
 raise SystemExit(1) from None
"""


def main():
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    rows = json.loads(
        subprocess.check_output(
            docker + ["inspect", "safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"],
            stderr=subprocess.DEVNULL,
        )
    )
    for row in rows:
        assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
        print(
            "LookupService="
            + json.dumps(
                {
                    "name": row["Name"],
                    "image": row["Config"]["Image"],
                    "health": row["State"]["Health"]["Status"],
                    "restarts": row["RestartCount"],
                }
            ),
            flush=True,
        )
    environment = dict(item.split("=", 1) for item in rows[0]["Config"]["Env"] if "=" in item)
    flags = {
        key: environment.get(key, "")
        for key in ("GROUP_HELP_ENABLED", "MODERATION_MTPROTO_ENABLED")
    }
    print("LookupRuntimeFlags=" + json.dumps(flags, sort_keys=True), flush=True)
    assert flags == {"GROUP_HELP_ENABLED": "false", "MODERATION_MTPROTO_ENABLED": "true"}
    if sys.argv[1:] == ["--config-only"]:
        return
    assert rows[0]["Config"]["Image"] == "safecheck:2.15.4-5c0fe59a4788"
    root = Path(rows[0]["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    command = docker + [
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
        PROBE,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=100, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    safe = json.loads(lines[-1]) if lines else {"verification_error": "NoResult"}
    print("PublicLookupVerification=" + json.dumps(safe, sort_keys=True), flush=True)
    if result.returncode or "verification_error" in safe:
        raise RuntimeError("Public lookup verification failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("LookupVerification=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

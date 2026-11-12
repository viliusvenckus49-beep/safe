"""Read-only AUTO ADS counts and editor metadata. Never prints content or tokens. No messages or mutations."""

import json
import subprocess
from pathlib import Path

PROBE = r"""
import asyncio,json
from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy import select,text
from app.ads_content import validate_content,units
from app.config import Settings
from app.db import create_database
from app.errors import DomainError
from app.models import AdCampaign,AdContentVersion

async def main():
 cfg=Settings();engine,sessions=create_database(cfg.database_url)
 try:
  async with sessions() as session:
   await session.execute(text('SET TRANSACTION READ ONLY'))
   rows=await session.execute(select(AdCampaign.id,AdCampaign.version,AdContentVersion.content).join(AdContentVersion,(AdContentVersion.campaign_id==AdCampaign.id)&(AdContentVersion.revision==AdCampaign.version)).order_by(AdCampaign.id).limit(50))
   counts=[]
   for cid,revision,c in rows:
    value=c.get('text','');entities=c.get('entities',[])
    try:validate_content(c);error=None
    except DomainError as e:error=e.code
    counts.append({'ad_id':cid,'version':revision,'characters':len(value),'utf16_units':units(value),'media_count':len(c.get('media',[])),'custom_emoji':sum(e.get('type')=='custom_emoji' for e in entities),'entity_count':len(entities),'validation_error':error})
   await session.rollback()
  storage=RedisStorage.from_url(cfg.redis_url.get_secret_value())
  try:
   bot_id=Bot(cfg.bot_token.get_secret_value()).id
   key=StorageKey(bot_id=bot_id,chat_id=cfg.group_owner,user_id=cfg.group_owner)
   data=await storage.get_data(key)
   editor={k:data[k] for k in ('q_cid','q_version','q_field','q_kind','ads_campaign','ads_base','ads_field') if k in data and isinstance(data[k],(str,int,bool,type(None)))}
  finally:await storage.close()
  return {'content_counts':counts,'owner_editor':editor,'readonly':True}
 finally:await engine.dispose()
try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),60)),sort_keys=True))
except Exception as error:
 print(json.dumps({'audit_error':type(error).__name__}))
 raise SystemExit(1) from None
"""


def main():
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    row = json.loads(
        subprocess.check_output(docker + ["inspect", "safecheck-bot-1"], stderr=subprocess.DEVNULL)
    )[0]
    print(
        "AdsContentRuntime="
        + json.dumps(
            {
                "image": row["Config"]["Image"],
                "health": row["State"].get("Health", {}).get("Status"),
            }
        ),
        flush=True,
    )
    root = Path(row["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    cmd = docker + [
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=190, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    safe = json.loads(lines[-1]) if lines else {"audit_error": "MissingResult"}
    if result.returncode or "audit_error" in safe:
        print("AdsContentAudit=" + json.dumps(safe), flush=True)
        raise SystemExit(1)
    for key, value in safe.items():
        for item in value if isinstance(value, list) else [value]:
            print(
                "AdsContentAudit=" + json.dumps({"kind": key, "data": item}, sort_keys=True),
                flush=True,
            )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AdsContentAudit=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

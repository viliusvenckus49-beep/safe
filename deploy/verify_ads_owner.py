"""Read-only live verification of owner-only AUTO ADS authority in approved groups."""

import json
import subprocess
import sys
from pathlib import Path

PROBE = r"""
import asyncio,json
from aiogram import Bot
from sqlalchemy import select,text
from app.admin_services import AdminService
from app.ads_publication import publication_rights
from app.ads_unified import UnifiedAds
from app.bot.session import telegram_session
from app.config import Settings
from app.db import create_database
from app.errors import DomainError
from app.models import AdSchedule

async def main():
 cfg=Settings();assert cfg.group_owner==8896102663
 assert not cfg.group_help_enabled and cfg.moderation_mtproto_enabled
 engine,sessions=create_database(cfg.database_url)
 try:
  async with Bot(cfg.bot_token.get_secret_value(),session=telegram_session(cfg)) as bot:
   me=await bot.get_me();assert me.id==9002888138
   async with sessions() as session:
    await session.execute(text('SET TRANSACTION READ ONLY'))
    assert AdminService(cfg,session).is_owner(cfg.group_owner)
    management=UnifiedAds(cfg,session)
    groups=await management.eligible(cfg.group_owner)
    audience=[tuple(r) for r in (await session.execute(select(AdSchedule.id,AdSchedule.campaign_id,AdSchedule.chat_id,AdSchedule.excluded).order_by(AdSchedule.id))).all()]
    candidate=await session.scalar(select(AdSchedule.campaign_id).where(AdSchedule.authorized_by==cfg.group_owner,AdSchedule.excluded.is_(False)).order_by(AdSchedule.id.desc()).limit(1))
    await session.commit()
    verified=[]
    for group in groups:
     await management.pub.group(group.chat_id)
     await session.commit()
     await publication_rights(bot,group.chat_id,cfg.group_owner,owner_id=cfg.group_owner)
     member=await bot.get_chat_member(group.chat_id,cfg.group_owner)
     status=getattr(member.status,'value',member.status)
     verified.append({'chat_id':group.chat_id,'owner_status':status,'ads_authorized':True})
    target_groups=await management.preflight(cfg.group_owner,candidate,bot) if candidate else []
    after=[tuple(r) for r in (await session.execute(select(AdSchedule.id,AdSchedule.campaign_id,AdSchedule.chat_id,AdSchedule.excluded).order_by(AdSchedule.id))).all()]
    assert audience==after
    assert await session.scalar(text('SELECT version_num FROM alembic_version'))=='0025'
    await session.rollback()
    denied=False
    for actor in sorted(cfg.admins-{cfg.group_owner}):
     for group in groups:
      try:member=await bot.get_chat_member(group.chat_id,actor)
      except Exception:continue
      if member.status in ('creator','administrator'):continue
      try:await publication_rights(bot,group.chat_id,actor,owner_id=cfg.group_owner)
      except DomainError as error:
       assert error.code=='ap_rights';denied=True;break
      else:raise RuntimeError('A non-owner bypassed group rights')
     if denied:break
    return {'bot_id':me.id,'owner':cfg.group_owner,'approved_groups':verified,'preflight_ad':candidate,'preflight_groups':len(target_groups),'audiences_unchanged':True,'non_owner_denial_verified':denied,'staff_sending':False,'id_lookup':True,'schema':'0025'}
 finally:await engine.dispose()

try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),100)),sort_keys=True))
except Exception as error:
 print(json.dumps({'verification_error':type(error).__name__}))
 raise SystemExit(1) from None
"""


def main():
    expected = sys.argv[1]
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
            "AdsOwnerService="
            + json.dumps(
                {
                    "name": row["Name"],
                    "image": row["Config"]["Image"],
                    "health": row["State"]["Health"]["Status"],
                }
            ),
            flush=True,
        )
    assert rows[0]["Config"]["Image"].endswith("-" + expected[:12])
    root = Path(rows[0]["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    assert root.name == expected
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=130, check=False)
    lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
    safe = json.loads(lines[-1]) if lines else {"verification_error": "MissingResult"}
    print("AdsOwnerVerification=" + json.dumps(safe, sort_keys=True), flush=True)
    if result.returncode or "verification_error" in safe:
        raise RuntimeError("Owner publishing verification failed")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AdsOwnerVerification=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

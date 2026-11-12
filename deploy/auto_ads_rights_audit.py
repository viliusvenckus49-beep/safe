"""Read-only production AUTO ADS identity/rights audit. No messages or mutations."""

import json
import subprocess
from pathlib import Path

PROBE = r"""
import asyncio,json,re
from aiogram import Bot
from sqlalchemy import select,text
from app.bot.session import telegram_session
from app.config import Settings
from app.db import create_database
from app.group_services import GroupService
from app.models import ManagedGroup,AdCampaign,AdSchedule,AdAssignmentPolicy,AdPublicationAttempt,AdGlobalOperation,AdGlobalOperationItem,Administrator,AuditEvent

def code(value):
 return value if value and re.fullmatch(r'[A-Za-z0-9_:.-]{1,96}',value) else ('OTHER_ERROR' if value else None)

async def main():
 cfg=Settings();engine,sessions=create_database(cfg.database_url)
 try:
  async with sessions() as session:
   await session.execute(text('SET TRANSACTION READ ONLY'))
   groups=[]
   for g in await session.scalars(select(ManagedGroup).order_by(ManagedGroup.chat_id)):
    groups.append({'chat_id':g.chat_id,'approved':g.approved,'enabled':g.enabled,'type':g.chat_type,'owner_removed':await GroupService(cfg,session).is_removed_group(g.chat_id)})
   schedules=[{'id':s.id,'ad_id':s.campaign_id,'chat_id':s.chat_id,'authorized_by':s.authorized_by,'status':s.status,'excluded':s.excluded,'last_error':code(s.last_error)} for s in await session.scalars(select(AdSchedule).order_by(AdSchedule.id.desc()).limit(80))]
   policies=[{'ad_id':p.campaign_id,'authorized_by':p.authorized_by,'mode':p.mode} for p in await session.scalars(select(AdAssignmentPolicy).order_by(AdAssignmentPolicy.campaign_id.desc()).limit(80))]
   attempts=[{'id':a.id,'schedule_id':a.schedule_id,'status':a.status,'error':code(a.error)} for a in await session.scalars(select(AdPublicationAttempt).order_by(AdPublicationAttempt.id.desc()).limit(12))]
   items=[{'operation_id':i.operation_id,'chat_id':i.chat_id,'status':i.status,'error':code(i.error)} for i in await session.scalars(select(AdGlobalOperationItem).where(AdGlobalOperationItem.error.is_not(None)).limit(16))]
   audits=[{'id':a.id,'actor_id':a.actor_id,'action':a.action,'ad_id':(a.details or {}).get('campaign_id')} for a in await session.scalars(select(AuditEvent).where(AuditEvent.action.startswith('ads_')).order_by(AuditEvent.id.desc()).limit(12))]
   database_admins=list(await session.scalars(select(Administrator.telegram_id).where(Administrator.active.is_(True))))
   schema=await session.scalar(text('SELECT version_num FROM alembic_version'))
   await session.rollback()
  actors=[]
  for identity in [cfg.group_owner,*[s['authorized_by'] for s in schedules if not s['excluded']],*[p['authorized_by'] for p in policies],*sorted(cfg.bootstrap_staff),*database_admins]:
   if identity and identity>0 and identity not in actors:actors.append(identity)
  actors=actors[:12]
  async with Bot(cfg.bot_token.get_secret_value(),session=telegram_session(cfg)) as bot:
   me=await bot.get_me();sem=asyncio.Semaphore(3)
   async def member(chat,identity):
    async with sem:
     try:
      m=await asyncio.wait_for(bot.get_chat_member(chat,identity),6)
      status=getattr(m.status,'value',m.status)
      return {'chat_id':chat,'identity':identity,'bot':identity==me.id,'status':status,'same_id':m.user.id==identity,'administrator':status in ('creator','administrator'),'restrict':getattr(m,'can_restrict_members',None),'post':getattr(m,'can_post_messages',None)}
     except Exception as error:return {'chat_id':chat,'identity':identity,'bot':identity==me.id,'error_type':type(error).__name__}
   rights=await asyncio.gather(*(member(g['chat_id'],i) for g in groups if g['approved'] and g['enabled'] and not g['owner_removed'] for i in [me.id,*actors]))
  return {'header':{'bot_id':me.id,'username':me.username,'owner':cfg.group_owner,'schema':schema,'staff_enabled':cfg.group_help_enabled,'id_lookup':cfg.moderation_mtproto_enabled},'groups':groups,'schedules':schedules,'policies':policies,'attempts':attempts,'operation_errors':items,'audit':audits,'rights':rights}
 finally:await engine.dispose()

try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),165)),sort_keys=True))
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
        "AdsRightsRuntime="
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
        print("AdsRightsAudit=" + json.dumps(safe), flush=True)
        raise SystemExit(1)
    for key, value in safe.items():
        for item in value if isinstance(value, list) else [value]:
            print(
                "AdsRightsAudit=" + json.dumps({"kind": key, "data": item}, sort_keys=True),
                flush=True,
            )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("AdsRightsAudit=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

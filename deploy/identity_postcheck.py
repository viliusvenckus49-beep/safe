"""Verify live identity and reconcile only the owner's seven confirmed bot-admin groups."""

import hashlib
import json
import subprocess
import types
from pathlib import Path

HELPER_SHA = "3f1056df87dc58cad9fe7413d0cf6c8499d3b375fd1d74aeadca9fbbf454b462"
STAGE = Path("/var/lib/safecheck/identity-change/024e843e595a46e68d7f6cfdf15dc39f")

PROBE = r"""
import asyncio,hashlib,json,sys
from aiogram import Bot
from sqlalchemy import select,text
from app.config import Settings
from app.bot.session import telegram_session
from app.db import create_database
from app.admin_services import AdminService
from app.group_services import GroupService
from app.models import ManagedGroup

async def main():
 p=json.load(sys.stdin);cfg=Settings()
 assert cfg.group_owner==8896102663
 assert hashlib.sha256(cfg.admin_ids.encode()).hexdigest()==p['admin_digest']
 engine,sessions=create_database(cfg.database_url)
 try:
  async with Bot(cfg.bot_token.get_secret_value(),session=telegram_session(cfg)) as bot:
   me=await bot.get_me()
   assert me.id==9002888138 and me.username.lower()=='crimsonprimebot'
   async with sessions() as session:
    admin=AdminService(cfg,session)
    assert admin.is_owner(cfg.group_owner) and await admin.is_admin(cfg.group_owner)
    assert not admin.is_owner(28563234)
    groups=GroupService(cfg,session)
    verified=[]
    for cid in (-1004467894566,-1004421084692,-1004413023527,-1004395482317,-1004383481928,-1003987480921,-1003976343267):
     member=await bot.get_chat_member(cid,me.id)
     assert member.status in ('administrator','creator') and bool(getattr(member,'can_restrict_members',False))
     assert not await groups.is_removed_group(cid)
     row=await session.get(ManagedGroup,cid,populate_existing=True)
     assert row is not None
     action='verified'
     if not (row.approved and row.enabled):
      # Only the seventh explicitly confirmed group may gain approval here.
      assert cid==-1004383481928
      owner=await bot.get_chat_member(cid,cfg.group_owner)
      assert owner.status in ('administrator','creator')
      chat=await bot.get_chat(cid)
      await groups.register_group(cfg.group_owner,cid,chat.title,True,str(chat.type))
      action='registered_by_owner'
     elif not row.can_restrict_members:
      await groups.note_group_permissions(cid,True)
      action='permissions_refreshed'
     verified.append({'chat_id':cid,'action':action})
    await session.commit()
    active=(await session.scalars(select(ManagedGroup).where(ManagedGroup.approved.is_(True),ManagedGroup.enabled.is_(True)))).all()
    assert len(active)==7 and all(g.can_restrict_members for g in active)
    for g in active:assert not await groups.is_removed_group(g.chat_id)
    schema=await session.scalar(text('SELECT version_num FROM alembic_version'))
    assert schema=='0025'
    return {'bot_id':me.id,'username':me.username,'owner':cfg.group_owner,'owner_access':True,'old_owner_role':False,'admin_ids_unchanged':True,'active_groups':len(active),'groups':verified,'schema':schema}
 finally:await engine.dispose()

try:print(json.dumps(asyncio.run(asyncio.wait_for(main(),90)),sort_keys=True))
except Exception as error:
 print(json.dumps({'postcheck_error':type(error).__name__}))
 raise SystemExit(1) from None
"""


def main():
    source = Path("/home/ubuntu/SAFECHECK_IDENTITY.py").read_bytes()
    if hashlib.sha256(source).hexdigest() != HELPER_SHA:
        raise RuntimeError("Identity helper checksum differs")
    helper = types.ModuleType("identity_prepare")
    exec(compile(source, "<verified identity helper>", "exec"), helper.__dict__)
    before = helper.scalar((STAGE / "settings.before").read_text(), "ADMIN_IDS")
    digest = hashlib.sha256(before.encode()).hexdigest()
    docker = ["docker", "--host=unix:///var/run/docker.sock"]
    inventory = json.loads(
        subprocess.check_output(
            docker + ["inspect", "safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"],
            stderr=subprocess.DEVNULL,
        )
    )
    for row in inventory:
        assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
        print(
            "IdentityLiveService="
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
    bot = inventory[0]
    assert bot["Config"]["Image"] == "safecheck:2.15.4-cbfe6208f29d"
    root = Path(bot["Config"]["Labels"]["com.docker.compose.project.working_dir"])
    args = docker + [
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
    result = subprocess.run(
        args,
        input=json.dumps({"admin_digest": digest}),
        capture_output=True,
        text=True,
        timeout=110,
        check=False,
    )
    lines = [x for x in result.stdout.splitlines() if x.startswith("{")]
    safe = json.loads(lines[-1]) if lines else {"postcheck_error": "MissingResult"}
    print("IdentityLiveVerification=" + json.dumps(safe, sort_keys=True), flush=True)
    if result.returncode or "postcheck_error" in safe:
        raise RuntimeError("Live verification incomplete")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("IdentityPostcheck=FAILED; Type=" + type(error).__name__, flush=True)
        raise SystemExit(1) from None

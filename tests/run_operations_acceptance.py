"""Disposable production Compose acceptance test; all credentials/data are fake."""

import hashlib
import importlib.util
import json
import os
import secrets
import tempfile
from pathlib import Path

from test_group_moderation_migration import SEED

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "safecheck-ops-qa-" + secrets.token_hex(4)
BASE = Path(tempfile.mkdtemp(prefix="safecheck-operations-qa-"))
BASE.mkdir(mode=0o700, exist_ok=True)
sec = BASE / "secrets"
sec.mkdir(mode=0o700, exist_ok=True)
pw, redis_pw, maintenance_pw = (secrets.token_hex(16) for _ in range(3))
files = {
    "postgres_password": pw,
    "redis_password": redis_pw,
    "redis_maintenance_password": maintenance_pw,
    "redis_acl": f"user default on >{redis_pw} ~* &* +@all -config -flushdb -flushall -shutdown -module -debug -acl\nuser maintenance on >{maintenance_pw} ~* &* +ping +flushdb\n",
    "runtime.env": f"BOT_TOKEN=123456:TEST_ONLY_NOT_A_REAL_TOKEN\nDATABASE_URL=postgresql+asyncpg://safecheck:{pw}@postgres:5432/safecheck\nREDIS_URL=redis://:{redis_pw}@redis:6379/0\n",
}
for name, data in files.items():
    p = sec / name
    if p.exists():
        p.chmod(0o600)
    p.write_text(data)
    p.chmod(0o444)  # Disposable fake secrets only; production permissions differ.
(BASE / "runtime.env").write_text("ADMIN_IDS=900\nGROUP_OWNER_ID=900\nLOG_LEVEL=INFO\n")
(BASE / "compose.env").write_text(
    f"SAFECHECK_IMAGE={os.environ.get('SAFECHECK_ACCEPTANCE_IMAGE', 'safecheck:2.15.4')}\nSAFECHECK_ENV_FILE={BASE}/runtime.env\nSAFECHECK_SECRETS_DIR={sec}\n"
)
(BASE / "mtproto").mkdir()
with (BASE / "compose.env").open("a") as compose_env:
    compose_env.write(f"SAFECHECK_MTPROTO_DIR={BASE}/mtproto\n")
runner = BASE / "fake_telegram.py"
runner.write_text("""import asyncio
from aiogram.client.session.base import BaseSession
from aiogram.types import User, WebhookInfo
from app import main
class FakeTelegram(BaseSession):
    def __init__(self,on_poll):
        super().__init__(); self.on_poll=on_poll
    async def close(self): pass
    async def stream_content(self,*args,**kwargs):
        yield b''
    async def make_request(self,bot,method,timeout=None):
        name=type(method).__name__
        if name=='GetMe':return User(id=123456,is_bot=True,first_name='QA',username='safecheck_qa_fake')
        if name=='GetWebhookInfo':return WebhookInfo(url='',pending_update_count=0,has_custom_certificate=False)
        if name=='GetUpdates':
            await asyncio.sleep(2)
            self.on_poll()
            return []
        if name in {'SetMyCommands','SendMessage','BanChatMember'}:return True
        raise RuntimeError('Unsupported fake Telegram method')
main.telegram_session=lambda settings, on_poll=None, **kwargs:FakeTelegram(on_poll)
main.run()
""")
runner.chmod(0o644)
entrypoint = BASE / "secret_entrypoint.py"
entrypoint.write_bytes((ROOT / "deploy/secret_entrypoint.py").read_bytes())
entrypoint.chmod(0o444)  # Public configuration code; no credentials.
qa = BASE / "override.yml"
qa.write_text(
    f"configs:\n  secret_entrypoint:\n    file: {entrypoint}\nservices:\n  bot:\n    command: [python, /qa/fake_telegram.py]\n    volumes:\n      - {runner}:/qa/fake_telegram.py:ro\n"
)
spec = importlib.util.spec_from_file_location("ops", ROOT / "deploy/ops.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
args = module.parser().parse_args(
    [
        "--root",
        str(ROOT),
        "--env-file",
        str(BASE / "compose.env"),
        "--project",
        PROJECT,
        "--backup-dir",
        str(BASE / "backups"),
        "--state-dir",
        str(BASE / "state"),
        "initialize",
    ]
)
ops = module.Operations(args)
ops.compose += ["-f", str(qa)]
os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
for selector in ("DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
    os.environ.pop(selector, None)
try:
    with ops.lock(maintenance=True):
        ops.initialize()
    states, ready = ops.status()
    if not ready:
        raise RuntimeError("Isolated Compose health failed")
    print(
        "Isolated production Compose, secret loading, Redis ACL/FSM and fake Telegram polling healthy.",
        flush=True,
    )
    # Terminal/inactive fixture rows cannot initiate publication or moderation.
    seed = SEED + [
        "INSERT INTO group_sanctions(id,chat_id,telegram_id,kind,actor_id,reason,status,policy_version,request_key,created_at,updated_at) VALUES(1,-100,51,'WARN',900,'QA','RECORDED',1,'qa-sanction',now(),now())",
        "INSERT INTO group_sanction_jobs(sanction_id,operation,actor_id,status,available_at,created_at,updated_at) VALUES(1,'APPLY',900,'SUCCEEDED',now(),now(),now())",
        "INSERT INTO ad_campaigns(id,internal_name,created_by,updated_by,request_key,created_at,updated_at) VALUES(1,'QA INTERNAL',900,900,'qa-campaign',now(),now())",
        "INSERT INTO ad_content_versions(id,campaign_id,revision,content,actor_id,request_key,created_at) VALUES(1,1,1,'{}',900,'qa-version',now())",
        "INSERT INTO ad_schedules(id,campaign_id,chat_id,status,authorized_by,updated_at) VALUES(1,1,-100,'PAUSED',900,now())",
        "INSERT INTO ad_assignment_policies(campaign_id,mode,authorized_by,updated_at) VALUES(1,'SELECTED',900,now())",
        "INSERT INTO ad_publication_attempts(id,schedule_id,content_version_id,chat_id,schedule_version,due_at,status,notified,created_at,completed_at) VALUES(1,1,1,-100,1,now(),'SENT',true,now(),now())",
        "INSERT INTO ad_publication_parts(attempt_id,position,kind,status,message_ids,tries) VALUES(1,0,'TEXT','SENT','[501]',1)",
        "INSERT INTO ad_customer_grants(id,campaign_id,customer_id,status,assigned_by,created_at) VALUES(1,1,42,'ACTIVE',900,now())",
        "INSERT INTO ad_customer_links(grant_id,token_hash,status,created_by,created_at) VALUES(1,repeat('a',64),'ACTIVE',900,now())",
        "INSERT INTO ad_content_drafts(id,campaign_id,actor_id,base_version,field,content,inputs,status,request_key,request_fingerprint,created_at,expires_at) VALUES(repeat('b',32),1,42,1,'TEXT','{}','[]','CANCELLED','qa-draft',repeat('c',64),now(),now())",
        "INSERT INTO ad_customer_drafts(draft_id,grant_id,campaign_id,customer_id) VALUES(repeat('b',32),1,1,42)",
        "INSERT INTO ad_global_operations(id,campaign_id,actor_id,action,status,policy_version,payload,request_key,created_at,expires_at) VALUES(repeat('d',32),1,900,'ASSIGN','DONE',1,'{}','qa-operation',now(),now())",
        "INSERT INTO ad_global_operation_items(operation_id,chat_id,schedule_id,status) VALUES(repeat('d',32),-100,1,'SUCCEEDED')",
    ]

    def sql(statement):
        return ops.dc(
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "safecheck",
            "-d",
            "safecheck",
            "-v",
            "ON_ERROR_STOP=1",
            "-Atc",
            statement,
        ).strip()

    for statement in seed:
        sql(statement)

    def fingerprint():
        names = (
            sql(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
            )
            .decode()
            .splitlines()
        )
        result = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sql(
                f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM {quoted} t"
            )
            result[name] = {
                "rows": len(json.loads(rows)),
                "sha256": hashlib.sha256(rows).hexdigest(),
            }
        return result

    def sequences():
        return sql(
            "SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY sequencename),'[]'::jsonb) FROM (SELECT sequencename,start_value,increment_by,last_value FROM pg_sequences WHERE schemaname='public') s"
        )

    before = fingerprint()
    before_sequences = sequences()
    with ops.lock():
        archive = ops.backup()
    assert archive.stat().st_size > 0 and archive.stat().st_mode & 0o777 == 0o600
    assert Path(str(archive) + ".sha256").stat().st_mode & 0o777 == 0o600
    # A saved draft must not remain after restoring an earlier DB snapshot.
    ops.dc(
        "exec",
        "-T",
        "redis",
        "sh",
        "-ec",
        'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli SET fsm:qa:draft stale >/dev/null',
    )
    sql("UPDATE users SET display_name='changed after backup' WHERE id=1")
    args.archive = archive
    args.confirm_restore = "safecheck"
    with ops.lock(maintenance=True):
        ops.restore()
    after = fingerprint()
    if before_sequences != sequences():
        raise RuntimeError("Restored PostgreSQL sequences differ")
    if before != after:
        raise RuntimeError(
            "Restored full-table fingerprints differ: "
            + repr([k for k in before if before[k] != after.get(k)])
        )
    print(
        "RESTORE35 "
        + json.dumps(
            {
                "tables": len(before),
                "nonempty_tables": sum(v["rows"] > 0 for v in before.values()),
                "rows": sum(v["rows"] for v in before.values()),
                "schema": "0017",
                "sequences_equal": True,
                "all_table_fingerprints_equal": True,
            }
        ),
        flush=True,
    )
    keys = ops.dc(
        "exec",
        "-T",
        "redis",
        "sh",
        "-ec",
        'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli EXISTS fsm:qa:draft',
    ).strip()
    if keys != b"0":
        raise RuntimeError("Stale FSM survived restore")
    args.alert_token_file = None
    args.alert_chat_id = None
    if not ops.monitor():
        raise RuntimeError("Monitor reported isolated failure")
    print(
        "Verified private backup, checksum, independent restore, rescue backup and stale FSM clearing passed.",
        flush=True,
    )
except Exception:
    output = ops.dc("logs", "--tail", "8", "bot").decode()
    print(output, flush=True)  # Fake-only QA transport; app structured logs redact errors.
    raise
finally:
    # Only this unique disposable project and its own volumes are removed.
    ops.dc("down", "--volumes", "--remove-orphans")
    print("Disposable QA project cleaned up; live services untouched.", flush=True)

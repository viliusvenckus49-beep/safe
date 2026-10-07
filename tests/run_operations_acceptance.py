"""Disposable production Compose acceptance test; all credentials/data are fake."""

import importlib.util
import os
import secrets
import tempfile
from pathlib import Path

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
    f"SAFECHECK_IMAGE=safecheck:2.13.1\nSAFECHECK_ENV_FILE={BASE}/runtime.env\nSAFECHECK_SECRETS_DIR={sec}\n"
)
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
main.telegram_session=lambda settings, on_poll=None:FakeTelegram(on_poll)
main.run()
""")
runner.chmod(0o644)
qa = BASE / "override.yml"
qa.write_text(
    f"services:\n  bot:\n    command: [python, /qa/fake_telegram.py]\n    volumes:\n      - {runner}:/qa/fake_telegram.py:ro\n"
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
    # Seed deterministic fake app data, then prove backup restore restores the prior state.
    ops.dc(
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
        "-c",
        "INSERT INTO users (id,telegram_id,username,display_name,created_at,updated_at) VALUES (100,42,'qaseeded','QA',now(),now());",
    )
    with ops.lock():
        archive = ops.backup()
    # A saved draft must not remain after restoring an earlier DB snapshot.
    ops.dc(
        "exec",
        "-T",
        "redis",
        "sh",
        "-ec",
        'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli SET fsm:qa:draft stale >/dev/null',
    )
    ops.dc(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "safecheck",
        "-d",
        "safecheck",
        "-c",
        "DELETE FROM users WHERE id=100;",
    )
    args.archive = archive
    args.confirm_restore = "safecheck"
    with ops.lock(maintenance=True):
        ops.restore()
    value = ops.dc(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "safecheck",
        "-d",
        "safecheck",
        "-Atc",
        "SELECT count(*) FROM users WHERE id=100;",
    ).strip()
    if value != b"1":
        raise RuntimeError("Restored fake data mismatch")
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

"""Read-only production diagnostics; never send Telegram messages or change data."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

D = ["docker", "--host=unix:///var/run/docker.sock"]

MENU_PROBE = """
import json
from app.bot import keyboards as base
from app.bot import merchant_keyboards as kb
from app.bot.callbacks import Merchant
from app.i18n import use_language
def routes(markup):
    return [Merchant.unpack(b.callback_data).action for row in markup.inline_keyboard
            for b in row if b.callback_data and b.callback_data.startswith('mt|')]
result={}
for lang in ('lt','en','ru'):
    with use_language(lang):
        public=routes(kb.root(False)); owner=routes(kb.root(True))
        assert public==['cities'] and owner==['cities','mine']
        assert not {'search','info','terms','begin'}.intersection(routes(kb.listing([],0,0)))
        admin=routes(kb.admin_root()); assert {'a_terms','a_search'} <= set(admin)
        home={b.callback_data for row in base.home().inline_keyboard for b in row}
        assert {base.action('info'),base.action('lookup')} <= home
        result[lang]={'public':public,'owner':owner,'admin_creation_and_search':True}
print(json.dumps(result,sort_keys=True))
"""

PROFILE_PROBE = """
import json
from datetime import UTC,datetime
from html import escape
from types import SimpleNamespace
from app import presentation as p
from app.bot import merchant_views as views
from app.i18n import t,use_language
owner=SimpleNamespace(id=1,telegram_id=1,username='format_probe',display_name='Format probe')
description='Legal & <description>'+chr(10)*2+'Second paragraph.'
entry=SimpleNamespace(name='Format probe',owner=owner,description=description,
                      city_code='vilnius',status='APPROVED',reason=None)
record=SimpleNamespace(id=1,report_id=None,created_at=datetime.now(UTC),reason='In-memory check')
result={}
for lang in ('lt','en','ru'):
 with use_language(lang):
  data=dict(user=owner,score=2,positive=3,negative=1,trusted=False,scam=None,unresolved_scam=None)
  card=views.profile(entry,data)
  quote='<blockquote><i>'+escape(description)+'</i></blockquote>'
  assert quote in card and quote in views.fields(entry) and quote in views.private_entry(entry)
  assert '<blockquote><i><b>' in card and '<b>+2</b>' in card
  assert t('mc.disclaimer') in card and t('p.lookup_warning') in p.profile(data)
  assert all(t(key) in card for key in ('mc.about','mc.rep_title','mc.security_title','mc.catalog_approved'))
  data['trusted']=True
  assert t('mc.disclaimer') not in views.profile(entry,data)
  assert t('p.lookup_warning') not in p.profile(data)
  assert '<b>'+t('mc.trusted')+'</b>' in views.profile(entry,data)
  for kind in ('scam','unresolved_scam'):
   data['scam']=data['unresolved_scam']=None;data[kind]=record
   assert t('mc.disclaimer') in views.profile(entry,data)
   assert t('p.lookup_warning') in p.profile(data)
   assert '<b>'+t('mc.trusted')+'</b>' not in views.profile(entry,data)
  assert entry.description==description
  result[lang]={'description_quote':True,'reputation_quote':True,'trusted_warning_rule':True,'scam_precedence':True}
print(json.dumps(result,sort_keys=True))
"""


def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=90)


def emit(key, value):
    print(key + "=" + json.dumps(value, sort_keys=True), flush=True)


def inspect(name):
    return json.loads(run(D + ["inspect", name]))[0]


def sql(query):
    output = run(
        D
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
            "BEGIN READ ONLY; " + query + "; COMMIT;",
        ]
    )
    return [line for line in output.splitlines() if line not in ("BEGIN", "COMMIT")]


def main():
    phase, sha = sys.argv[1:]
    assert phase in ("inspect", "verify") and re.fullmatch(r"[0-9a-f]{40}", sha)
    current = inspect("safecheck-bot-1")
    root = Path(current["Config"]["Labels"]["com.docker.compose.project.working_dir"]).parent / sha
    spec = importlib.util.spec_from_file_location(
        "release_diagnostic", root / "deploy/production_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    release = module.Release(root, sha)
    for cid in run(D + ["ps", "-aq"]).split():
        row = inspect(cid)
        emit(
            "ContainerInventory",
            {
                "name": row["Name"],
                "id": row["Id"],
                "image": row["Config"]["Image"],
                "started": row["State"]["StartedAt"],
                "running": row["State"]["Running"],
                "health": row["State"].get("Health", {}).get("Status"),
            },
        )
    for name in ("safecheck-bot-1", "safecheck-postgres-1", "safecheck-redis-1"):
        row = inspect(name)
        emit(
            "ServiceHealth", {"name": name, "health": row["State"].get("Health", {}).get("Status")}
        )
        assert row["State"]["Running"] and row["State"]["Health"]["Status"] == "healthy"
    emit("Schema", sql("SELECT version_num FROM alembic_version"))
    for table, condition in (
        ("group_sanction_jobs", module.BLOCKING_NATIVE_JOBS),
        ("group_sanctions", "status IN ('PENDING','REVOKING','UNCERTAIN')"),
        ("ad_publication_attempts", "status IN ('CLAIMED','SENDING','UNCERTAIN')"),
        ("group_check_events", "state IN ('PENDING','WAITING','SENDING','QUEUED')"),
    ):
        emit(
            "BlockingWork",
            {"table": table, "count": sql("SELECT count(*) FROM " + table + " WHERE " + condition)},
        )
    for label, check in (
        ("Idle", release.idle),
        ("Writers", release.writers),
        ("DataServices", release.preserve_services),
    ):
        try:
            check()
            emit("ReleaseGuard", {"check": label, "passed": True})
        except RuntimeError as error:
            # These guard messages are static reviewed strings, never command output.
            emit("ReleaseGuard", {"check": label, "passed": False, "reason": str(error)})
    if phase == "verify":
        row = inspect("safecheck-bot-1")
        assert row["Config"]["Image"].endswith("-" + sha[:12])
        assert sql("SELECT version_num FROM alembic_version") == ["0024"]
        for table in (
            "merchant_entries",
            "merchant_proposals",
            "merchant_search_grams",
            "merchant_settings",
        ):
            emit("CatalogRows", {"table": table, "count": sql("SELECT count(*) FROM " + table)})
        emit(
            "CatalogNavigation",
            json.loads(run(D + ["exec", "safecheck-bot-1", "python", "-c", MENU_PROBE])),
        )
        emit(
            "MerchantProfileFormatting",
            json.loads(run(D + ["exec", "safecheck-bot-1", "python", "-c", PROFILE_PROBE])),
        )
        emit("MerchantDeploymentVerified", {"sha": sha, "healthy": True})


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit("DiagnosticFailed", {"type": type(error).__name__})
        raise SystemExit(1) from None

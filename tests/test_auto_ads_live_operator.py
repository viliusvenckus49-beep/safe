"""Safety boundaries of the optional existing-sandbox operator; no real Telegram calls."""

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import uuid
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "ads_live_operator", ROOT / "tools/auto_ads_live_test.py"
)
op = importlib.util.module_from_spec(spec)
spec.loader.exec_module(op)

# Minimal faithful transport boundary of the original pinned runtime. The full
# predecessor and source hash maps are independently verified by predecessor().
RUNTIME = """
BANNED={'BanChatMember','UnbanChatMember','RestrictChatMember','PromoteChatMember'}
def session(*args,**kwargs):
    value=original(*args,**kwargs)
    async def guard(make_request,bot,method):
        kind=type(method).__name__;chat=getattr(method,'chat_id',None)
        if kind in BANNED:raise PermissionError('No consenting moderation target configured')
        if chat is not None and chat not in {-1004454398792,28563234}:raise PermissionError('Outside test allowlist')
        return await make_request(bot,method)
    value.middleware(guard);return value
"""


def test_payload_is_pinned_reviewed_current_runtime_not_new_dependencies():
    base = op.predecessor()
    patches = op.incoming(base)
    assert len(patches) == 81
    assert "app/bot/commands.py" in patches
    for name in op.PAYLOAD:
        assert patches[name] == (ROOT / name).read_bytes()
        assert (
            base.REVIEWED_SOURCE_SHA[op.CODE_HEAD][name]
            == hashlib.sha256(patches[name]).hexdigest()
        )
    assert not any("requirements" in name or "pyproject" in name for name in patches)
    assert "migrations/versions/0020_unified_auto_ads.py" in patches


def test_tampered_embedded_operator_refuses_execution(monkeypatch):
    monkeypatch.setattr(op, "LEGACY_SHA", "0" * 64)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        op.predecessor()


@pytest.mark.parametrize(
    ("requested", "context", "expected"),
    [
        (None, {}, None),
        (None, {"auto_ads_client_id": 42}, 42),
        (43, {"auto_ads_client_id": 42}, 43),
    ],
)
def test_code_update_preserves_existing_client_unless_explicitly_changed(
    requested, context, expected
):
    assert op.client_for(requested, context) == expected


@pytest.mark.parametrize("invalid", [True, "42", 0, -42, 8986848376])
def test_invalid_preserved_private_boundary_refuses_update(invalid):
    with pytest.raises(RuntimeError, match="human test account"):
        op.client_for(None, {"auto_ads_client_id": invalid})


@pytest.mark.parametrize("name", ["../.env", "/tmp/leak.py", "migrations/evil.py"])
def test_patch_destination_cannot_escape_source(monkeypatch, name):
    base = op.predecessor()
    entry = next(iter(op.PAYLOAD.values()))
    monkeypatch.setattr(op, "PAYLOAD", {name: entry})
    with pytest.raises(RuntimeError, match="destination"):
        op.incoming(base)


def test_corrupted_payload_refuses_source_write(monkeypatch):
    base = op.predecessor()
    source = b"raise SystemExit()"
    entry = {"content": base64.b64encode(zlib.compress(source)).decode(), "sha256": "0" * 64}
    monkeypatch.setattr(op, "PAYLOAD", {"app/evil.py": entry})
    with pytest.raises(RuntimeError, match="hash mismatch"):
        op.incoming(base)


@pytest.mark.parametrize(
    "kind,chat,user,allowed",
    [
        ("SendMessage", -1004454398792, None, True),
        ("SendPhoto", -1004454398792, None, True),
        ("SendVideo", -1004454398792, None, True),
        ("SendAnimation", -1004454398792, None, True),
        ("SendDocument", -1004454398792, None, True),
        ("SendMediaGroup", -1004454398792, None, True),
        ("SendMessage", 28563234, None, True),
        ("SendMessage", 42, None, True),
        ("SendMessage", 43, None, False),
        ("SendMessage", -999, None, False),
        ("BanChatMember", -1004454398792, 8896102663, True),
        ("RestrictChatMember", -1004454398792, 8896102663, True),
        ("UnbanChatMember", -1004454398792, 8896102663, True),
        ("BanChatMember", -1004454398792, 42, False),
        ("RestrictChatMember", -999, 8896102663, False),
        ("PromoteChatMember", -1004454398792, 8896102663, False),
    ],
)
def test_transport_only_allows_approved_group_private_accounts_and_native_target(
    kind, chat, user, allowed
):
    base = op.predecessor()
    source = op.runtime_for(base, SimpleNamespace(RUNTIME=RUNTIME), 42)
    transport = SimpleNamespace(middleware=lambda guard: setattr(transport, "guard", guard))
    namespace = {"original": lambda *args, **kwargs: transport}
    exec(compile(source, "<test runtime>", "exec"), namespace)
    namespace["session"]()
    method = type(kind, (), {"chat_id": chat, "user_id": user})()
    request = AsyncMock(return_value=True)
    if allowed:
        assert asyncio.run(transport.guard(request, None, method)) is True
        request.assert_awaited_once()
    else:
        from aiogram.exceptions import TelegramBadRequest

        with pytest.raises((PermissionError, TelegramBadRequest)):
            asyncio.run(transport.guard(request, None, method))
        request.assert_not_awaited()


def test_known_active_mute_does_not_block_ads_upgrade():
    state = dict(
        schema="0019",
        groups=1,
        active_ads=0,
        publications=0,
        ambiguous_sanctions=0,
        jobs=0,
        active_sanctions=3,
    )
    assert op.idle(SimpleNamespace(database_json=lambda *args: state), None) == state


@pytest.mark.parametrize("field", ["active_ads", "publications", "ambiguous_sanctions", "jobs"])
def test_inflight_or_unknown_work_is_preserved_without_retry_or_cleanup(field):
    state = dict(
        schema="0019",
        groups=1,
        active_ads=0,
        publications=0,
        ambiguous_sanctions=0,
        jobs=0,
        active_sanctions=0,
    )
    state[field] = 1
    with pytest.raises(RuntimeError, match="no state deleted"):
        op.idle(SimpleNamespace(database_json=lambda *args: state), None)
    assert op.idle(SimpleNamespace(database_json=lambda *args: state), None, require_idle=False)
    assert not any(sql in op.WORK_SQL for sql in ("UPDATE ", "DELETE ", "INSERT ", "ALTER "))


@pytest.mark.parametrize("case", ["valid", "wrong_id", "wrong_name", "webhook", "actor", "bot"])
def test_live_preflight_reads_bot_and_actor_but_never_target_or_sends(monkeypatch, capsys, case):
    import aiogram

    calls = []

    class Bot:
        def __init__(self, token):
            self.session = SimpleNamespace(close=AsyncMock())

        async def get_me(self):
            calls.append("get_me")
            return SimpleNamespace(
                id=1 if case == "wrong_id" else 8986848376,
                username="wrong" if case == "wrong_name" else "RedTrustBot",
            )

        async def get_webhook_info(self):
            calls.append("get_webhook_info")
            return SimpleNamespace(url="active" if case == "webhook" else "")

        async def get_chat_member(self, chat, user):
            calls.append((chat, user))
            assert user in {28563234, 8986848376}
            status = "creator" if user == 28563234 else "administrator"
            if case == ("actor" if user == 28563234 else "bot"):
                status = "left"
            return SimpleNamespace(user=SimpleNamespace(id=user), status=status)

    monkeypatch.setattr(aiogram, "Bot", Bot)
    import io

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"token": "TEST_ONLY"})))
    namespace = {}
    exec(compile(op.PREFLIGHT, "<preflight>", "exec"), namespace)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is (case == "valid")
    assert all(c in {"get_me", "get_webhook_info"} or c[0] == -1004454398792 for c in calls)


def test_migration_task_selects_only_named_rehearsal_database_and_private_stdin(tmp_path):
    calls = []

    def docker(args, **kwargs):
        calls.append((args, kwargs))
        return "" if args[0] == "ps" else "ADS_MIGRATION24=PASS; ExistingRowsVerified=True"

    kit = SimpleNamespace(
        ROOT=tmp_path,
        PREFIX="owned-test",
        BASE_IMAGE="image",
        docker=docker,
        common=lambda *args: [],
        mounts=lambda: ["--mount", f"type=bind,src={tmp_path}/source,dst=/app,readonly"],
    )
    env = {"DATABASE_URL": "postgresql+asyncpg://test:SECRET@owned-test-postgres/safecheck_staging"}
    op.run_task(
        None,
        kit,
        {"image": "sha256:pinned"},
        env,
        db_name="auto_ads_rehearsal_test",
        source=tmp_path / "candidate",
    )
    args, kwargs = calls[-1]
    assert "SECRET" not in " ".join(args)
    assert json.loads(kwargs["data"])["DATABASE_URL"].endswith("/auto_ads_rehearsal_test")
    assert f"type=bind,src={tmp_path}/candidate,dst=/app,readonly" in args
    assert env["DATABASE_URL"].endswith("/safecheck_staging")


@pytest.mark.parametrize("revision", ["0016", "0019", "0020", "0021", "0022", "0023", "0024"])
async def test_real_migration_operator_retains_legacy_rows_and_policy_history(revision):
    import asyncpg

    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Requires dedicated TEST_POSTGRES_URL; never uses production configuration")
    admin = await asyncpg.connect(url.replace("postgresql+asyncpg:", "postgresql:"))
    name = "ads_live_qa_" + uuid.uuid4().hex
    await admin.execute('CREATE DATABASE "' + name + '"')
    test_url = url.rsplit("/", 1)[0] + "/" + name
    env = {**os.environ, "DATABASE_URL": test_url}
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", revision],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr
        db = await asyncpg.connect(test_url.replace("postgresql+asyncpg:", "postgresql:"))
        try:
            seed = """
                INSERT INTO ad_campaigns(id,internal_name,created_by,updated_by,request_key,created_at,updated_at)
                VALUES(1,'Keep content',900,900,'preserve',now(),now());
                INSERT INTO ad_assignment_policies(campaign_id,mode,authorized_by,updated_at)
                VALUES(1,'FUTURE',900,now());
                INSERT INTO audit_events(actor_id,action,details,created_at)
                VALUES(900,'preserve','{}',now());
            """
            if revision in {"0020", "0021", "0022", "0023", "0024"}:
                seed = seed.replace("'FUTURE'", "'EXISTING'")
            if revision in {"0021", "0022", "0023", "0024"}:
                seed += """
                    INSERT INTO managed_groups(chat_id,chat_type,title,enabled,approved,can_restrict_members,updated_at)
                    VALUES(-100,'supergroup','Keep policy',true,true,true,now());
                    INSERT INTO group_moderation_settings(chat_id,warning_limit,warning_action,warning_mute_seconds,warning_version,updated_at)
                    VALUES(-100,5,'BAN',3600,4,now());
                """
            if revision in {"0022", "0023", "0024"}:
                seed += """
                    INSERT INTO group_check_settings(chat_id,actions,spam_terms,updated_by,updated_at)
                    VALUES(-100,'{\"username\":\"OFF\"}','[]',900,now());
                """
            await db.execute(seed)
        finally:
            await db.close()
        script = op.MIGRATE.replace("'/app/alembic.ini'", repr(str(ROOT / "alembic.ini")))
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", script],
            input=json.dumps({"DATABASE_URL": test_url}),
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=50,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ADS_MIGRATION24=PASS; ExistingRowsVerified=True" in result.stdout
    finally:
        await admin.execute('DROP DATABASE "' + name + '" WITH (FORCE)')
        await admin.close()


@pytest.mark.parametrize("success", [True, False])
def test_backup_rehearsal_has_readable_code_private_dump_and_scoped_cleanup(
    tmp_path, monkeypatch, success
):
    base = op.predecessor()
    (tmp_path / "source" / "app").mkdir(parents=True, mode=0o700)
    original = tmp_path / "source" / "app" / "keep.py"
    original.write_text("UNCHANGED = True\n")
    original.chmod(0o600)
    context = {"head": "original"}
    commands = []

    def private_json(path, value):
        base.write_file(path, json.dumps(value).encode(), 0o600)

    def docker(args, **kwargs):
        commands.append(args)
        return "-- private TEST ONLY SQL dump\n" if "pg_dump" in args else ""

    kit = SimpleNamespace(
        ROOT=tmp_path,
        PREFIX="test-only",
        docker=docker,
        private_json=private_json,
        private_read=lambda path: context,
    )

    def rehearsal(*args, **kwargs):
        source = kwargs["source"]
        assert kwargs["db_name"].startswith("auto_ads_rehearsal_")
        assert (source / "app").stat().st_mode & 0o777 == 0o755
        assert (source / "app" / "keep.py").stat().st_mode & 0o777 == 0o644
        assert (source / "app" / "nested").stat().st_mode & 0o777 == 0o755
        assert (source / "app" / "nested" / "new.py").read_bytes() == b"PASS = True\n"
        if not success:
            raise RuntimeError("Rehearsal failed")

    monkeypatch.setattr(op, "run_task", rehearsal)
    if success:
        backup = op.backup_and_rehearse(base, kit, {}, {}, {"app/nested/new.py": b"PASS = True\n"})
    else:
        with pytest.raises(RuntimeError, match="Rehearsal failed"):
            op.backup_and_rehearse(base, kit, {}, {}, {"app/nested/new.py": b"PASS = True\n"})
        backup = next(tmp_path.glob("auto-ads-backup-*"))
    assert backup.stat().st_mode & 0o777 == 0o700
    assert (backup / "database.sql").stat().st_mode & 0o777 == 0o600
    assert original.read_text() == "UNCHANGED = True\n"
    assert original.stat().st_mode & 0o777 == 0o600
    dropped = [args[-1] for args in commands if "dropdb" in args]
    assert bool(dropped) is success
    assert all(name.startswith("auto_ads_rehearsal_") for name in dropped)


PREFLIGHT_ARCHIVE_CASES = [
    ("DRAFT", "ACTIVE", False, None, True),
    ("ARCHIVED", "ACTIVE", False, None, False),
    ("DRAFT", "PAUSED", False, None, False),
    ("DRAFT", "ACTIVE", True, None, False),
    ("ARCHIVED", "ACTIVE", False, "PENDING", True),
    ("ARCHIVED", "ACTIVE", False, "UNCERTAIN", True),
    ("ARCHIVED", "ACTIVE", False, "DEFERRED", True),
]


async def archived_schedule_preflight(
    sessions,
    settings,
    campaign_status,
    schedule_status,
    excluded,
    publication_status,
    blocked,
    *,
    postgres=False,
):
    from sqlalchemy import text

    from app.ads_services import AdsService
    from app.models import (
        AdCampaign,
        AdContentVersion,
        AdPublicationAttempt,
        AdSchedule,
        ManagedGroup,
        now,
    )

    async with sessions() as session:
        campaign = AdCampaign(
            internal_name="Existing ad", created_by=900, updated_by=900, request_key="keep"
        )
        session.add_all([campaign, ManagedGroup(chat_id=-1004454398792, title="Test only")])
        await session.flush()
        schedule = AdSchedule(
            campaign_id=campaign.id,
            chat_id=-1004454398792,
            status=schedule_status,
            interval_seconds=1800,
            start_at=now(),
            next_run_at=now(),
            authorized_by=900,
            excluded=excluded,
        )
        session.add(schedule)
        await session.flush()
        cid, sid, version = campaign.id, schedule.id, campaign.version
        if publication_status:
            content = AdContentVersion(
                campaign_id=cid, revision=1, content={}, actor_id=900, request_key="keep-content"
            )
            session.add(content)
            await session.flush()
            session.add(
                AdPublicationAttempt(
                    schedule_id=sid,
                    content_version_id=content.id,
                    chat_id=-1004454398792,
                    schedule_version=1,
                    due_at=now(),
                    status=publication_status,
                )
            )
        await session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        await session.execute(text("INSERT INTO alembic_version VALUES ('0019')"))
        await session.commit()
        if campaign_status == "ARCHIVED":
            # Reproduce the actual old delete/archive flow, rather than inventing
            # a manually patched database state. It deliberately retains history.
            await AdsService(settings, session).archive(900, cid, version)
        query = op.WORK_SQL.removeprefix("BEGIN READ ONLY;").removesuffix(" COMMIT;")
        if not postgres:
            query = query.replace("json_build_object(", "json_object(")
        state = await session.scalar(text(query))
        if isinstance(state, str):
            state = json.loads(state)
        assert state["active_ads"] == int(
            campaign_status == "DRAFT" and schedule_status == "ACTIVE" and not excluded
        )
        assert state["archived_active_schedules"] == int(
            campaign_status == "ARCHIVED" and schedule_status == "ACTIVE"
        )
        base = SimpleNamespace(database_json=lambda *args: state)
        if blocked:
            with pytest.raises(RuntimeError, match="no state deleted"):
                op.idle(base, None)
        else:
            assert op.idle(base, None) == state
        assert (
            await session.get(AdSchedule, sid, populate_existing=True)
        ).status == schedule_status
        assert (
            await session.get(AdCampaign, cid, populate_existing=True)
        ).status == campaign_status


@pytest.mark.parametrize(
    "campaign_status,schedule_status,excluded,publication_status,blocked", PREFLIGHT_ARCHIVE_CASES
)
async def test_real_sql_preflight_distinguishes_archived_ads_from_live_work(
    database, settings, campaign_status, schedule_status, excluded, publication_status, blocked
):
    await archived_schedule_preflight(
        database, settings, campaign_status, schedule_status, excluded, publication_status, blocked
    )


@pytest.mark.parametrize(
    "campaign_status,schedule_status,excluded,publication_status,blocked", PREFLIGHT_ARCHIVE_CASES
)
async def test_postgres_preflight_distinguishes_archived_ads_from_live_work(
    postgres_contract, campaign_status, schedule_status, excluded, publication_status, blocked
):
    sessions, settings = postgres_contract
    await archived_schedule_preflight(
        sessions,
        settings,
        campaign_status,
        schedule_status,
        excluded,
        publication_status,
        blocked,
        postgres=True,
    )


def test_warning_policy_update_retains_exact_previous_auto_ads_source_manifest():
    base = op.predecessor()
    previous = "c83b09695e05189252bce94ee0ebe032ec207b2c"
    assert base.REVIEWED_SOURCE_SHA[previous] == op.PREVIOUS_ADS_SOURCE_SHA[previous]
    assert (
        base.REVIEWED_SOURCE_SHA[previous]["app/ads_content.py"]
        != base.REVIEWED_SOURCE_SHA[op.CODE_HEAD]["app/ads_content.py"]
    )
    assert (
        base.REVIEWED_SOURCE_SHA[previous]["app/models.py"]
        == op.PREVIOUS_ADS_SOURCE_SHA["ecfa4a193a9178b59c768835dd50cea6bd892233"]["app/models.py"]
    )
    assert (
        base.REVIEWED_SOURCE_SHA[previous]["app/models.py"]
        != base.REVIEWED_SOURCE_SHA[op.CODE_HEAD]["app/models.py"]
    )
    assert (
        "migrations/versions/0021_group_warning_settings.py"
        in base.REVIEWED_SOURCE_SHA[op.CODE_HEAD]
    )


def test_previous_auto_ads_runtime_is_verified_without_widening_client_scope(tmp_path):
    base = op.predecessor()
    kit = SimpleNamespace(ROOT=tmp_path, RUNTIME=RUNTIME)
    path = tmp_path / "runtime" / "runtime.py"
    path.parent.mkdir()
    path.write_text(op.runtime_for(base, kit, 42))
    context = {"head": next(iter(op.PREVIOUS_ADS_SOURCE_SHA)), "auto_ads_client_id": 42}
    assert op.verify_runtime(base, kit, context) == path.read_text()
    with pytest.raises(RuntimeError, match="Unreviewed"):
        op.verify_runtime(base, kit, {**context, "auto_ads_client_id": 43})


def test_checks_notice_work_blocks_operator_without_deleting_state():
    state = dict(
        schema="0022",
        groups=1,
        active_ads=0,
        publications=0,
        ambiguous_sanctions=0,
        jobs=0,
        active_sanctions=0,
    )

    def read(kit, sql):
        return {"checks_pending": 1} if "checks_pending" in sql else dict(state)

    with pytest.raises(RuntimeError, match="no state deleted"):
        op.idle(SimpleNamespace(database_json=read), None)

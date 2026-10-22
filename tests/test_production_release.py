"""Production release safety contracts; no live server or Telegram writes."""

import importlib.util
import json
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from test_integration_contracts import postgres_contract as postgres_contract

ROOT = Path(__file__).parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


release = module("production_release")
probe = module("production_probe")


def test_mount_order_is_not_a_data_service_change_but_mount_content_is():
    row = {
        "Id": "id",
        "Image": "image",
        "Config": {"Image": "postgres"},
        "HostConfig": {},
        "Mounts": [
            {"Destination": "/data", "Source": "existing-data", "RW": True},
            {"Destination": "/run/secrets/password", "Source": "existing-secret", "RW": False},
        ],
    }
    reordered = deepcopy(row)
    reordered["Mounts"].reverse()
    assert release.data_fingerprint(row) == release.data_fingerprint(reordered)
    for field in ("Source", "RW"):
        changed = deepcopy(reordered)
        changed["Mounts"][0][field] = "changed"
        assert release.data_fingerprint(row) != release.data_fingerprint(changed)


@pytest.mark.parametrize("payload", [b"native-pg-dump-bytes", b""])
def test_backup_creates_private_file_and_checks_its_digest(tmp_path, payload):
    value = release.Release.__new__(release.Release)
    value.backup_dir = tmp_path
    value.ops = module("ops")

    def dump(*args, stdout):
        assert args[:4] == ("exec", "-T", "postgres", "pg_dump")
        stdout.write(payload)

    value.original = SimpleNamespace(dc=dump)
    if not payload:
        with pytest.raises(RuntimeError, match="Empty production backup"):
            value.backup("frozen")
        assert not (tmp_path / "frozen.dump.sha256").exists()
        return
    path = value.backup("frozen")
    assert path.read_bytes() == payload and path.stat().st_mode & 0o777 == 0o600
    assert Path(str(path) + ".sha256").read_text().strip() == value.ops.digest(path)
    assert Path(str(path) + ".sha256").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        value.backup("frozen")
    assert path.read_bytes() == payload


async def test_postgres_probe_detects_a_changed_legacy_user(postgres_contract):
    import asyncpg

    from app.services import Service

    factory, settings = postgres_contract
    async with factory() as session:
        schema = await session.scalar(text("SELECT current_schema()"))
        await session.execute(
            text("CREATE TABLE alembic_version(version_num varchar(32) PRIMARY KEY)")
        )
        await session.execute(text("INSERT INTO alembic_version VALUES('0023')"))
        await session.commit()
        await Service(settings, session).observe(42, "original", "Original")
    connection = await asyncpg.connect(
        settings.database_url.replace("postgresql+asyncpg:", "postgresql:"),
        server_settings={"search_path": schema},
    )
    try:
        baseline = await probe.evidence(connection)
        probe.verify(baseline, await probe.evidence(connection, baseline))
        async with factory() as session:
            await Service(settings, session).observe(42, "changed", "Changed")
        with pytest.raises(RuntimeError, match="Retained rows changed"):
            probe.verify(baseline, await probe.evidence(connection, baseline))
    finally:
        await connection.close()


@pytest.mark.parametrize("revision", range(10, 23))
def test_release_accepts_only_pinned_complete_migrations(revision):
    candidate = release.migrations(ROOT)
    previous = {name: value for name, value in candidate.items() if int(name[:4]) <= revision}
    assert release.compatible(candidate, previous)
    for name in release.PINS:
        bad = {**candidate, name: candidate[name] + b"#changed"}
        assert not release.compatible(bad, previous)
    assert not release.compatible({**candidate, "0023_extra.py": b"new"}, previous)


@pytest.mark.parametrize("part", ["postgres", "redis", "volumes", "secrets"])
def test_data_service_and_credential_replacement_refused(part):
    old = {
        "services": {"postgres": {"image": "pg"}, "redis": {"image": "redis"}},
        "volumes": {"data": {}},
        "secrets": {"runtime": {"file": "private"}},
    }
    new = deepcopy(old)
    release.unchanged_data_services(old, new)
    (new["services"] if part in {"postgres", "redis"} else new)[part] = {"changed": True}
    with pytest.raises(RuntimeError):
        release.unchanged_data_services(old, new)


def test_retention_hash_preserves_types_row_boundaries_and_order():
    def rows(*values):
        return [{"item": json.dumps(v)} for v in values]

    one = probe.fingerprint(rows([1, "a"], [2, "b"]), ["id", "text"])
    assert one == probe.fingerprint(rows([2, "b"], [1, "a"]), ["id", "text"])
    assert one != probe.fingerprint(rows(["1", "a"], [2, "b"]), ["id", "text"])
    assert one != probe.fingerprint(rows([1, "ab"], [2, ""]), ["id", "text"])


def test_only_documented_future_policy_transform_is_accepted():
    columns = ["campaign_id", "mode", "version", "auto_activate", "reconcile_at"]
    old = [{"item": json.dumps([1, "FUTURE", 3, True, "2026-10-20"])}]
    new = [{"item": json.dumps([1, "EXISTING", 4, False, None])}]
    expected = probe.fingerprint(old, columns, transform=True, table="ad_assignment_policies")
    assert expected == probe.fingerprint(new, columns)
    assert expected != probe.fingerprint(old, columns)
    assert probe.fingerprint(old, columns, transform=True, table="other") == probe.fingerprint(
        old, columns
    )


@pytest.mark.parametrize("change", ["head", "missing", "changed", "deleted"])
def test_retention_fails_on_changed_or_missing_legacy_rows(change):
    baseline = {
        "head": "0010",
        "tables": {
            "users": {
                "original": {"count": 2, "sha256": "same"},
                "expected": {"count": 2, "sha256": "same"},
            }
        },
    }
    actual = {"head": "0023", "tables": {"users": {"original": {"count": 2, "sha256": "same"}}}}
    probe.verify(baseline, actual)
    if change == "head":
        actual["head"] = "0010"
    elif change == "missing":
        actual["tables"].clear()
    elif change == "changed":
        actual["tables"]["users"]["original"]["sha256"] = "changed"
    else:
        actual["tables"]["users"]["original"]["count"] = 1
    with pytest.raises(RuntimeError):
        probe.verify(baseline, actual)


@pytest.mark.parametrize(
    "failure", [None, "prepare", "online", "frozen", "migrate", "readiness", "health"]
)
def test_release_never_restores_database_or_clears_redis_on_failure(tmp_path, failure):
    value = release.Release.__new__(release.Release)
    events = []

    def step(name):
        events.append(name)
        if failure == name:
            raise RuntimeError("injected failure")

    def dc(*args):
        if args[0] == "stop":
            step("stop")
        elif args[0] == "run":
            step("migrate")
        elif args[0] == "up":
            step("health")

    value.old = {"Id": "old-id", "Image": "old-image"}
    value.sha, value.image = "a" * 40, "new-image"
    value.root, value.backup_dir, value.state = tmp_path, tmp_path, tmp_path
    value.prepare = lambda: step("prepare")
    value.backup = lambda label: tmp_path / label
    value.rehearsal = lambda path: step("online" if "online" in path.name else "frozen") or {}
    value.inspect = lambda name: {"Id": "old-id", "State": {"Running": events.count("stop") == 0}}
    value.preserve_services = lambda: step("data-services")
    value.writers = lambda: step("writers")
    value.idle = lambda: step("idle")
    value.original = SimpleNamespace(
        lock=lambda **kwargs: nullcontext(),
        dc=dc,
        args=SimpleNamespace(root=tmp_path),
        persist_image=lambda image: step("persist"),
    )
    value.candidate = SimpleNamespace(
        dc=dc, compose=["docker", "compose"], readiness=lambda: step("readiness")
    )
    value.ops = SimpleNamespace(private_write=Mock())
    value.probe = lambda *args: step("retention")
    value.run = lambda command: events.append(tuple(command))
    if failure:
        with pytest.raises(RuntimeError, match="injected"):
            value.apply()
        assert "persist" not in events
    else:
        value.apply()
        assert (
            events.index("frozen")
            < events.index("migrate")
            < events.index("retention")
            < events.index("health")
            < events.index("persist")
        )
    if failure in {"prepare", "online"}:
        assert "stop" not in events
    if failure == "frozen":
        assert ("docker", "start", "old-id") in events
    if failure in {"migrate", "readiness", "health"}:
        assert ("docker", "start", "old-id") not in events
    assert not any(
        "DROP SCHEMA" in str(event) or "FLUSHDB" in str(event) or "pg_restore" in str(event)
        for event in events
    )

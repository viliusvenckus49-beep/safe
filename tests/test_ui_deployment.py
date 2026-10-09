"""Deployment refuses production credentials and touches only the preview service."""

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "deploy/test_ui_deploy.py"
    spec = importlib.util.spec_from_file_location("test_ui_deployment_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "CONFIG", tmp_path / "config/runtime.env")
    monkeypatch.setattr(module, "DATA", tmp_path / "state")
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *args: None)
    monkeypatch.setattr(module.sys, "argv", [str(path), "a" * 40])
    monkeypatch.setattr(
        module.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b"654321:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz")),
    )
    module.real_production_identity = module.production_identity
    monkeypatch.setattr(module, "production_identity", lambda: {"bot_id": 123456, "admins": "900"})
    monkeypatch.setattr(module, "production_snapshot", lambda: b"unchanged production images")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[-3:] == ["config", "--format", "json"]:
            return json.dumps({"services": {"studio": {}}}).encode()
        if args[1] == "inspect":
            return b"healthy"
        if args[1] == "logs":
            return b'{"event":"test_ui_started","username":"CrimsonPreviewBot"}'
        return b"passed"

    monkeypatch.setattr(module, "run", run)
    return module, calls


def test_production_bot_token_is_rejected_before_build_or_write(deployment, monkeypatch):
    module, calls = deployment
    monkeypatch.setattr(
        module.sys,
        "stdin",
        SimpleNamespace(buffer=io.BytesIO(b"123456:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz")),
    )
    with pytest.raises(RuntimeError, match="production bot"):
        module.main()
    assert calls == [] and not module.CONFIG.exists() and not module.DATA.exists()


def test_reuse_uses_installed_test_token_and_preserves_design_and_editor_roles(
    deployment, monkeypatch, capsys
):
    module, calls = deployment
    module.CONFIG.parent.mkdir()
    token = "654321:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz"
    module.CONFIG.write_text("TEST_UI_BOT_TOKEN=" + token + "\nSAFECHECK_TEST_IMAGE=old\n")
    module.DATA.mkdir()
    old_access = {"version": 1, "admins": [901], "editors": [888]}
    (module.DATA / "access.json").write_text(json.dumps(old_access))
    monkeypatch.setattr(
        module.sys,
        "argv",
        [module.__file__, "a" * 40, "--reuse-config", "--grant-admin", "8425927753"],
    )
    module.main()
    stored = json.loads((module.DATA / "access.json").read_text())
    assert stored["admins"] == [901, 8425927753] and stored["editors"] == [888]
    assert token in module.CONFIG.read_text()
    assert token not in capsys.readouterr().out
    assert len(list(module.DATA.glob("access-backup-*.json"))) == 1
    assert all(token not in " ".join(args) for args in calls)


def test_failed_update_restores_previous_editor_access(deployment, monkeypatch):
    module, _ = deployment
    module.DATA.mkdir()
    access = module.DATA / "access.json"
    previous = b'{"version":1,"admins":[],"editors":[888]}'
    access.write_bytes(previous)
    monkeypatch.setattr(
        module.sys, "argv", [module.__file__, "a" * 40, "--grant-admin", "8425927753"]
    )
    snapshots = iter([b"before", b"changed"])
    monkeypatch.setattr(module, "production_snapshot", lambda: next(snapshots))
    with pytest.raises(RuntimeError, match="Production service identity changed"):
        module.main()
    assert access.read_bytes() == previous


def test_reused_production_token_is_still_rejected(deployment, monkeypatch):
    module, calls = deployment
    module.CONFIG.parent.mkdir()
    module.CONFIG.write_text("TEST_UI_BOT_TOKEN=123456:TEST_TOKEN_abcdefghijklmnopqrstuvwxyz\n")
    monkeypatch.setattr(module.sys, "argv", [module.__file__, "a" * 40, "--reuse-config"])
    with pytest.raises(RuntimeError, match="production bot"):
        module.main()
    assert calls == []


def test_preview_deployment_uses_only_isolated_project_and_retains_design(deployment, capsys):
    module, calls = deployment
    module.DATA.mkdir()
    (module.DATA / "design.json").write_text('{"saved": "design"}')
    module.main()
    compose = [args for args in calls if args[:2] == ["docker", "compose"]]
    assert compose and all("safecheck-ui-studio" in args for args in compose)
    assert all("docker-compose.production.yml" not in " ".join(args) for args in calls)
    assert all(
        not any(value in args for value in ("postgres", "redis", "migrate", "down"))
        for args in compose
    )
    assert any(args[-4:] == ["up", "-d", "--no-deps", "studio"] for args in compose)
    assert (module.DATA / "design.json").read_text() == '{"saved": "design"}'
    assert len(list(module.DATA.glob("design-backup-*.json"))) == 1
    config = module.CONFIG.read_text()
    assert "TEST_UI_ADMIN_IDS=900" in config and "DATABASE_URL" not in config
    assert module.CONFIG.stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert "ProductionContainersUnchanged=true" in output
    assert "https://t.me/CrimsonPreviewBot" in output
    assert "TEST_TOKEN" not in output


def test_rendering_probe_is_offline_and_does_not_share_live_bot_memory(deployment):
    module, calls = deployment
    module.main()
    probe = next(args for args in calls if args[:2] == ["docker", "run"])
    assert probe[probe.index("--network") + 1] == "none"
    assert "readonly" in probe[probe.index("--mount") + 1]
    assert "--env-file" not in probe and "--env" not in probe
    start = next(args for args in calls if args[-4:] == ["up", "-d", "--no-deps", "studio"])
    assert calls.index(probe) < calls.index(start)
    assert not any(args[:3] == ["docker", "exec", "safecheck-ui-studio-studio-1"] for args in calls)


def test_changed_production_containers_fail_verification_and_stop_only_new_studio(
    deployment, monkeypatch
):
    module, calls = deployment
    snapshots = iter([b"before", b"changed"])
    monkeypatch.setattr(module, "production_snapshot", lambda: next(snapshots))
    with pytest.raises(RuntimeError, match="Production service identity changed"):
        module.main()
    assert any(args[-2:] == ["stop", "studio"] for args in calls)
    assert not module.CONFIG.exists()


def test_existing_preview_config_and_image_restored_on_failure(deployment, monkeypatch):
    module, calls = deployment
    module.CONFIG.parent.mkdir()
    old = b"SAFECHECK_TEST_IMAGE=safecheck-ui:previous\nTEST_UI_BOT_TOKEN=old\n"
    module.CONFIG.write_bytes(old)
    snapshots = iter([b"before", b"changed"])
    monkeypatch.setattr(module, "production_snapshot", lambda: next(snapshots))
    with pytest.raises(RuntimeError):
        module.main()
    assert module.CONFIG.read_bytes() == old
    assert sum(args[-4:] == ["up", "-d", "--no-deps", "studio"] for args in calls) == 2


def test_unexpected_compose_services_are_rejected(deployment, monkeypatch):
    module, calls = deployment
    run = module.run

    def injected(args, **kwargs):
        if args[-3:] == ["config", "--format", "json"]:
            return b'{"services":{"studio":{},"postgres":{}}}'
        return run(args, **kwargs)

    monkeypatch.setattr(module, "run", injected)
    with pytest.raises(RuntimeError, match="unexpected services"):
        module.main()
    assert not any("up" in args for args in calls)


def test_production_identity_uses_official_secret_loader_without_exposing_credentials(
    deployment, monkeypatch
):
    module, _ = deployment
    loaded = []

    def loader():
        loaded.append(True)

    monkeypatch.setattr("runpy.run_path", lambda path: {"load_secrets": loader})

    def settings():
        assert loaded
        return SimpleNamespace(
            bot_token=SimpleNamespace(get_secret_value=lambda: "123456:DO_NOT_OUTPUT"),
            admin_ids="900",
        )

    monkeypatch.setattr("app.config.Settings", settings)

    def execute(args, **kwargs):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            exec(args[-1], {})
        assert "DO_NOT_OUTPUT" not in buffer.getvalue()
        return buffer.getvalue().encode()

    monkeypatch.setattr(module, "run", execute)
    assert module.real_production_identity() == {"bot_id": 123456, "admins": "900"}

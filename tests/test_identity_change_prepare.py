"""Read-only identity preparation never changes active credentials or the database."""

import importlib.util
import json
import stat
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "prepare_identity", Path(__file__).resolve().parents[1] / "deploy/prepare_identity_change.py"
)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
TOKEN = "777:NEW_TOKEN_SENTINEL"
OLD = "666:OLD_TOKEN_SENTINEL"


@pytest.fixture
def kit(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    (root / "docker-compose.production.yml").write_text("name: safecheck\n")
    cfg = tmp_path / "runtime.env"
    cfg.write_text(
        'ADMIN_IDS="900,901"\nGROUP_OWNER_ID=900\nGROUP_HELP_ENABLED=true\nMODERATION_MTPROTO_ENABLED=false\n'
    )
    creds = tmp_path / "credentials" / "runtime.env"
    creds.parent.mkdir()
    creds.write_text(
        f"BOT_TOKEN={OLD}\nDATABASE_URL=postgresql+asyncpg://user:DB_SENTINEL@postgres/safecheck\nREDIS_URL=redis://:REDIS_SENTINEL@redis/0\n"
    )
    creds.chmod(0o400)
    selectors = tmp_path / "compose.env"
    selectors.write_text(f"SAFECHECK_ENV_FILE={cfg}\nSAFECHECK_SECRETS_DIR={creds.parent}\n")
    counts = {
        "schema": "0025",
        "groups": 10,
        "active_ads": 1,
        "media_versions": 2,
        "pending_sanctions": 1,
        "pending_ads": 0,
        "pending_checks": 0,
        "report_media": 3,
    }
    identity = {"bot_id": 777, "username": "NextSafeBot", "webhook_present": False}
    calls = []

    def run(args, **kw):
        calls.append((args, kw))
        if "inspect" in args:
            return json.dumps(
                [
                    {
                        "Config": {
                            "Labels": {
                                "com.docker.compose.project": "safecheck",
                                "com.docker.compose.project.working_dir": str(root),
                            }
                        },
                        "Image": "sha256:current_image",
                    }
                ]
            ).encode()
        if "compose" in args:
            assert json.loads(kw["data"])["token"] == TOKEN
            return json.dumps(identity).encode()
        assert "BEGIN READ ONLY;" in args[-1]
        return ("BEGIN\n" + json.dumps(counts) + "\nCOMMIT\n").encode()

    monkeypatch.setattr(prep, "run", run)
    return selectors, tmp_path / "staged", cfg, creds, identity, calls


def test_preparation_preserves_admins_active_credentials_and_originals(kit):
    selectors, stage, cfg, creds, _, calls = kit
    before = cfg.read_bytes(), creds.read_bytes()
    target, report = prep.prepare(selectors, stage, TOKEN, 902, "@NextSafeBot")
    assert (cfg.read_bytes(), creds.read_bytes()) == before
    assert (target / "settings.before").read_bytes() == before[0]
    assert (target / "credentials.before").read_bytes() == before[1]
    settings = (target / "settings.next").read_text()
    assert 'ADMIN_IDS="900,901"' in settings and "GROUP_OWNER_ID=902" in settings
    assert "GROUP_HELP_ENABLED=true" in settings  # Staff relay is not our own bot ID.
    new = (target / "credentials.next").read_text()
    assert TOKEN in new and "DB_SENTINEL" in new and "REDIS_SENTINEL" in new
    assert report["active_files_changed"] is False and report["admin_ids_unchanged"]
    assert report["mtproto_enabled"] and report["database_counts"]["media_versions"] == 2
    assert TOKEN not in json.dumps(report) and "DB_SENTINEL" not in json.dumps(report)
    for file in target.iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    for args, kwargs in calls:
        assert TOKEN not in str(args) and TOKEN not in str(kwargs.get("env", {}))
    assert len(calls) == 3


@pytest.mark.parametrize("field,value", [("bot_id", 999), ("username", "OtherBot")])
def test_wrong_bot_identity_never_stages_or_replaces(kit, field, value):
    selectors, stage, cfg, creds, identity, _ = kit
    identity[field] = value
    before = cfg.read_bytes(), creds.read_bytes()
    with pytest.raises(prep.PreparationError, match="Tokenas neatitinka"):
        prep.prepare(selectors, stage, TOKEN, 902, "NextSafeBot")
    assert (cfg.read_bytes(), creds.read_bytes()) == before
    assert not stage.exists()


@pytest.mark.parametrize("owner", [0, -1, 9223372036854775808, "@user", "１２"])
def test_invalid_owner_has_no_actions(kit, owner):
    selectors, stage, *_, calls = kit
    with pytest.raises(prep.PreparationError):
        prep.prepare(selectors, stage, TOKEN, owner, "NextSafeBot")
    assert not calls and not stage.exists()


def test_no_database_or_cache_mutation_commands():
    assert prep.COUNTS.startswith("BEGIN READ ONLY;")
    assert not {"UPDATE", "DELETE", "INSERT", "TRUNCATE", "DROP"}.intersection(prep.COUNTS.split())
    assert "get_me" in prep.PROBE and "get_webhook_info" in prep.PROBE
    for forbidden in (
        "start_polling",
        "send_message",
        "delete_webhook",
        "set_my_commands",
        "GroupHelpRelay",
    ):
        assert forbidden not in prep.PROBE


def test_env_patch_preserves_comments_crlf_and_does_not_evaluate_shell():
    before = 'ADMIN_IDS="900,901"\r\nGROUP_OWNER_ID=900 # previous\r\nUNRELATED=$(touch /never)\r\n'
    after = prep.changed(before, "GROUP_OWNER_ID", "902")
    assert after == before.replace("900 # previous", "902")
    assert prep.scalar(after, "ADMIN_IDS") == "900,901"
    with pytest.raises(prep.PreparationError):
        prep.changed(before + "GROUP_OWNER_ID=903\n", "GROUP_OWNER_ID", "902")


def test_unsafe_secret_file_rejected_before_token_probe(kit):
    selectors, stage, _, creds, _, calls = kit
    creds.chmod(0o644)
    with pytest.raises(prep.PreparationError):
        prep.prepare(selectors, stage, TOKEN, 902, "NextSafeBot")
    assert len(calls) == 1 and not stage.exists()


def test_subprocess_stderr_tokens_never_reach_the_exception(monkeypatch):
    def fail(*args, **kwargs):
        from types import SimpleNamespace

        assert "DOCKER_HOST" not in kwargs["env"]
        return SimpleNamespace(returncode=1, stdout=b"", stderr=TOKEN.encode())

    monkeypatch.setenv("DOCKER_HOST", "tcp://wrong-daemon")
    monkeypatch.setattr(prep.subprocess, "run", fail)
    with pytest.raises(prep.PreparationError) as error:
        prep.run(["docker", "--host=unix:///var/run/docker.sock", "inspect", "bot"])
    assert TOKEN not in str(error.value)


def test_secret_symlink_is_not_followed(tmp_path):
    actual = tmp_path / "secret"
    actual.write_text("BOT_TOKEN=never_read")
    actual.chmod(0o400)
    link = tmp_path / "link"
    link.symlink_to(actual)
    with pytest.raises(prep.PreparationError):
        prep.read_regular(link, secret=True)

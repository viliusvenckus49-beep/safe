import importlib.util
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

p = Path(__file__).resolve().parents[2] / "deploy/rotate_prepared_identity.py"
s = importlib.util.spec_from_file_location("rotate_identity", p)
m = importlib.util.module_from_spec(s)
s.loader.exec_module(m)


@pytest.fixture
def rig(tmp_path):
    paths = (tmp_path / "settings", tmp_path / "credentials")
    data = {
        "settings.before": b"ADMIN_IDS=900,901\nGROUP_OWNER_ID=900\n",
        "credentials.before": b"BOT_TOKEN=OLD_SECRET\nDATABASE_URL=keep\n",
        "settings.next": b"ADMIN_IDS=900,901\nGROUP_OWNER_ID=902\n",
        "credentials.next": b"BOT_TOKEN=NEW_SECRET\nDATABASE_URL=keep\n",
    }
    paths[0].write_bytes(data["settings.before"])
    paths[0].chmod(0o600)
    paths[1].write_bytes(data["credentials.before"])
    paths[1].chmod(0o444)
    return paths, data


@pytest.mark.parametrize(
    "failure",
    [None, "frozen-backup", "prepare", "identity", "readiness", "retention", "start", "persist"],
)
def test_cutover_success_or_failure_keeps_data_and_config_modes(rig, tmp_path, failure):
    paths, data = rig
    calls = []
    state = {"running": True}

    def step(name):
        calls.append(name)
        if failure == name:
            raise RuntimeError("injected failure")

    @contextmanager
    def lock(**kw):
        assert kw == {"maintenance": True}
        step("lock")
        yield

    def dc(*args):
        calls.append(args)
        if args[0] == "stop":
            state["running"] = False
        elif args[0] == "up":
            step("start" if state.get("candidate") else "rollback-start")
            state["running"] = True

    def backup(label):
        step("frozen-backup" if label == "identity-frozen" else "online-backup")
        return tmp_path / label

    def prepare():
        assert paths[0].read_bytes() == data["settings.next"]
        assert paths[1].read_bytes() == data["credentials.next"]
        state["candidate"] = True
        step("prepare")

    def original_persist(image):
        if image == "new":
            step("persist")
        else:
            state["candidate"] = False
            step("rollback-image")

    original = SimpleNamespace(lock=lock, dc=dc, persist_image=original_persist)
    r = SimpleNamespace(
        root=tmp_path,
        old={"Id": "original", "Config": {"Image": "old"}},
        original=original,
        candidate=SimpleNamespace(dc=dc, readiness=lambda: step("readiness")),
        ops=SimpleNamespace(
            private_write=lambda *a: step("record"),
            private_directory=lambda p: step("private-directory"),
        ),
        state=tmp_path,
        backup_dir=tmp_path / "backups",
        sha="a" * 40,
        image="new",
        idle=lambda: step("idle"),
        writers=lambda: step("writers"),
        preserve_services=lambda: step("preserve"),
        run=lambda args: step("build"),
        rehearsal=lambda p: step("rehearse") or {},
        backup=backup,
        inspect=lambda name: {"Id": "original", "State": {"Running": state["running"]}},
        prepare=prepare,
    )
    kwargs = {
        "identity_check": lambda *a: step("identity"),
        "retention_check": lambda *a: step("retention"),
    }
    if failure:
        with pytest.raises(RuntimeError):
            m.activate(
                r, paths, data, {"bot_id": 777, "username": "NewBot", "owner": 902}, **kwargs
            )
        assert paths[0].read_bytes() == data["settings.before"]
        assert paths[1].read_bytes() == data["credentials.before"]
        assert state["running"]
    else:
        m.activate(r, paths, data, {"bot_id": 777, "username": "NewBot", "owner": 902}, **kwargs)
        assert paths[0].read_bytes() == data["settings.next"]
        assert paths[1].read_bytes() == data["credentials.next"]
        assert calls.index("online-backup") < calls.index(("stop", "--timeout", "45", "bot"))
        assert calls.index("retention") < calls.index("start")
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert paths[1].stat().st_mode & 0o777 == 0o444
    assert all(
        "migrate" not in str(c) and "flush" not in str(c) and "restore" not in str(c) for c in calls
    )


def test_changed_original_aborts_before_actions(rig, tmp_path):
    paths, data = rig
    paths[0].write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="changed after"):
        m.activate(SimpleNamespace(), paths, data, {})


def test_writer_guard_retries_the_real_check_but_never_bypasses_it():
    attempts = []

    def transient():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("Another running container shares the production network")

    m.wait_for_readers(transient, pause=lambda seconds: None)
    assert len(attempts) == 3


def test_writer_guard_still_rejects_a_persistent_unknown_container():
    attempts = []

    def persistent():
        attempts.append(1)
        raise RuntimeError("Another running container shares the production network")

    with pytest.raises(RuntimeError):
        m.wait_for_readers(persistent, pause=lambda seconds: None)
    assert len(attempts) == 16

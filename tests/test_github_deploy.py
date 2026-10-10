import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "github_deploy", Path(__file__).parents[1] / "deploy/github_deploy.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_migrations_compare_contents_and_ignore_bytecode(tmp_path):
    versions = tmp_path / "migrations/versions"
    versions.mkdir(parents=True)
    (versions / "0001.py").write_text("schema")
    (versions / "0001.pyc").write_bytes(b"cache")
    assert module.migration_snapshot(tmp_path) == {"0001.py": b"schema"}


class Operations:
    def __init__(self, failure=False):
        self.args = SimpleNamespace(image=None)
        self.failure = failure
        self.events = []

    def deploy(self):
        self.events.append("deploy")
        if self.failure:
            raise RuntimeError("failed health")

    def rollback(self):
        self.events.append(("rollback", self.args.image))

    def persist_image(self, image):
        self.events.append(("persist", image))


def test_success_persists_image_only_after_deploy():
    candidate, original = Operations(), Operations()
    module.switch_release(candidate, original, "new", "old")
    assert candidate.events == ["deploy"]
    assert original.events == [("persist", "new")]


def test_failure_restores_previous_image_and_stays_failed():
    candidate, original = Operations(True), Operations()
    with pytest.raises(RuntimeError):
        module.switch_release(candidate, original, "new", "old")
    assert candidate.events == ["deploy", ("rollback", "old")]
    assert original.events == [("persist", "old")]


def test_only_reviewed_additive_profile_schema_is_allowed():
    root = Path(__file__).parents[1]
    current = module.migration_snapshot(root)
    previous = {name: value for name, value in current.items() if not name.startswith("0010_")}
    assert module.compatible_migrations(current, previous)
    assert module.compatible_migrations(current, current)
    assert not module.compatible_migrations({**current, "evil.py": b"drop tables"}, previous)
    assert not module.compatible_migrations({**current, "0001_initial.py": b"changed"}, previous)
    assert not module.compatible_migrations(
        {**current, "0010_redsafe_profile.py": b"changed"}, previous
    )

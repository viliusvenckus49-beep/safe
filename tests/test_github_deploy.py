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


@pytest.mark.parametrize("revision", range(9, 17))
def test_only_reviewed_release_migrations_are_allowed(revision):
    root = Path(__file__).parents[1]
    current = module.migration_snapshot(root)
    previous = {name: value for name, value in current.items() if int(name[:4]) <= revision}
    assert module.compatible_migrations(current, previous)
    assert module.compatible_migrations(current, current)
    assert not module.compatible_migrations({**current, "evil.py": b"drop tables"}, previous)
    assert not module.compatible_migrations({**current, "0001_initial.py": b"changed"}, previous)


@pytest.mark.parametrize("revision", range(10, 17))
@pytest.mark.parametrize("change", ["modified", "removed", "renamed"])
def test_reviewed_migrations_cannot_be_modified_removed_or_renamed(revision, change):
    current = module.migration_snapshot(Path(__file__).parents[1])
    name = next(name for name in current if name.startswith(f"{revision:04d}_"))
    candidate = dict(current)
    if change == "modified":
        candidate[name] += b"\n# unreviewed change\n"
    else:
        original = candidate.pop(name)
        if change == "renamed":
            candidate[f"{revision:04d}_renamed.py"] = original
    # Once deployed, every byte and filename must remain intact.
    assert not module.compatible_migrations(candidate, current)
    previous = {key: value for key, value in current.items() if int(key[:4]) < revision}
    assert not module.compatible_migrations(candidate, previous)


def test_unreviewed_next_migration_is_rejected_from_actual_production_base():
    current = module.migration_snapshot(Path(__file__).parents[1])
    previous = {name: value for name, value in current.items() if int(name[:4]) <= 10}
    candidate = {**current, "0017_unreviewed.py": b"# not part of the reviewed release"}
    assert not module.compatible_migrations(candidate, previous)

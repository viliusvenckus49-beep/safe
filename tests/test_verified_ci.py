import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verified_ci", Path(__file__).parents[1] / "deploy/verified_ci.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REPOSITORY = "viliusvenckus49-beep/safe"
SHA = "a" * 40
BRANCH = "safecheck-production-3-0"
REPORT = {
    "status": "completed",
    "conclusion": "success",
    "event": "push",
    "path": ".github/workflows/ci.yml",
    "head_branch": BRANCH,
    "head_sha": SHA,
    "repository": {"full_name": REPOSITORY},
    "head_repository": {"full_name": REPOSITORY},
}


def test_verified_source_matches_complete_passed_ci():
    assert module.verified_sha(REPORT, REPOSITORY, BRANCH, SHA) == SHA


@pytest.mark.parametrize("field", list(REPORT))
def test_unfinished_failed_wrong_source_branch_or_fork_is_refused(field):
    report = deepcopy(REPORT)
    report[field] = {"full_name": "other/repo"} if field.endswith("repository") else "wrong"
    with pytest.raises(ValueError):
        module.verified_sha(report, REPOSITORY, BRANCH, SHA)


@pytest.mark.parametrize("sha", ["main", "a" * 39, "A" * 40, "a" * 40 + "\n"])
def test_only_exact_commit_selector_is_accepted(sha):
    with pytest.raises(ValueError):
        module.verified_sha(REPORT, REPOSITORY, BRANCH, sha)

"""Attest the exact same-repository source already tested by the complete CI workflow."""

import argparse
import json
import os
import re
from pathlib import Path


def verified_sha(run, repository, branch, expected):
    if not re.fullmatch(r"[0-9a-f]{40}", expected):
        raise ValueError("A full source SHA is required")
    checks = {
        "status": "completed",
        "conclusion": "success",
        "event": "push",
        "path": ".github/workflows/ci.yml",
        "head_branch": branch,
        "head_sha": expected,
    }
    if any(run.get(key) != value for key, value in checks.items()):
        raise ValueError("Complete CI evidence does not match this release")
    if any(
        run.get(key, {}).get("full_name") != repository for key in ("repository", "head_repository")
    ):
        raise ValueError("CI evidence is from another repository")
    return expected


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("repository")
    parser.add_argument("branch")
    parser.add_argument("expected")
    args = parser.parse_args()
    sha = verified_sha(
        json.loads(args.report.read_text()), args.repository, args.branch, args.expected
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("sha=" + sha + "\n")
    print("VerifiedCompleteCI=PASS; ExactSourceSHA=" + sha)

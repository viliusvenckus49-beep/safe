#!/usr/bin/env python3
"""Deploy an uploaded, tested release to the existing VPS without replacing data services."""

import importlib.util
import os
import re
import sys
from pathlib import Path


def migration_snapshot(root):
    return {
        path.name: path.read_bytes()
        for path in (root / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    }


def switch_release(candidate, original, image, old_image):
    """Caller holds the shared maintenance lock; preserve the original selector on failure."""
    try:
        candidate.deploy()
        original.persist_image(image)
    except Exception:
        candidate.args.image = old_image
        candidate.rollback()
        original.persist_image(old_image)
        raise


def main():
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9a-f]{40}", sys.argv[1]):
        raise SystemExit("A full tested Git commit SHA is required")
    if os.geteuid() != 0:
        raise SystemExit("Run through sudo with private server configuration")
    sha = sys.argv[1]
    root = Path(__file__).resolve().parent.parent
    module_spec = importlib.util.spec_from_file_location("safecheck_ops", root / "deploy/ops.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    args = module.parser().parse_args(["--root", str(root), "deploy"])
    candidate = module.Operations(args)
    old_root = Path(
        candidate.run(
            [
                "docker",
                "inspect",
                "--format",
                '{{index .Config.Labels "com.docker.compose.project.working_dir"}}',
                "safecheck-bot-1",
            ]
        )
        .decode()
        .strip()
    )
    if not old_root.is_absolute() or not (old_root / "docker-compose.production.yml").is_file():
        raise module.OperationError("Existing production deployment could not be located")
    # This workflow intentionally supports code-only updates. Schema releases need a separate review.
    if migration_snapshot(root) != migration_snapshot(old_root):
        raise module.OperationError("Schema differs: code-only deployment refused")
    original_args = module.parser().parse_args(["--root", str(old_root), "deploy"])
    original = module.Operations(original_args)
    image = "safecheck:2.13.1-" + sha[:12]
    candidate.run(["docker", "build", "-t", image, str(root)])
    print("Candidate Docker build passed.", flush=True)
    selector = args.state_dir / "github-candidate.env"
    with candidate.lock(maintenance=True):
        old_image = (
            candidate.run(["docker", "inspect", "--format", "{{.Image}}", "safecheck-bot-1"])
            .decode()
            .strip()
        )
        module.private_write(selector, args.env_file.read_text())
        args.env_file = selector
        try:
            candidate.persist_image(image)
            # Compare resolved data service configurations without displaying secret values.
            import json

            before = json.loads(original.dc("config", "--format", "json"))
            after = json.loads(candidate.dc("config", "--format", "json"))
            for service in ("postgres", "redis"):
                if before["services"][service] != after["services"][service]:
                    raise module.OperationError("Data service configuration differs; refused")
            print("Schema and data service compatibility passed.", flush=True)
            switch_release(candidate, original, image, old_image)
            print("Verified backup, deployment and health checks passed.", flush=True)
        finally:
            selector.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never display subprocess stderr, environment, URLs or private key material.
        print("Deployment failed; inspect health and rollback status.", file=sys.stderr)
        raise SystemExit(1) from None

#!/usr/bin/env python3
"""Deploy an uploaded, tested release to the existing VPS without replacing data services."""

import hashlib
import importlib.util
import os
import re
import sys
import tomllib
from pathlib import Path


def migration_snapshot(root):
    return {
        path.name: path.read_bytes()
        for path in (root / "migrations" / "versions").glob("*.py")
        if path.name != "__init__.py"
    }


def compatible_migrations(candidate, existing):
    """Allow byte-pinned reviewed releases; never rewrite or remove old migrations."""
    if any(candidate.get(name) != content for name, content in existing.items()):
        return False
    reviewed = {
        "0010_redsafe_profile.py": "47431ef8207005336c163c73c611ecadf70dafdeb03597f7aedc76df5e2ae214",
        "0011_group_moderation.py": "9e8e28f9842dee7990043ddc42ce95ee6dc3ab1a3fff3dab0949be7f70249b8d",
        "0012_group_commands.py": "9f4c0fd8f71f1b693d6fb656d26d076784cc721a4a9f4db4bfd31dc4d9ce9ccd",
        "0013_ad_content.py": "3f8950cbe91ab655aafd7759b6e9629d83990144a0b3036b79915fd573589136",
        "0014_ad_publication.py": "08c05f8fbcca3e3fb576e5498d24768e758493744f4432443f52cee90ab1082f",
        "0015_global_ads_assignments_operations_and_.py": "1e1d787a2e15ed775d0798982063e50010573d91915c22af44c0d005d613d3d0",
        "0016_ad_customer_capabilities_and_hashed_.py": "07aa974edd1f6f74b6b284d2c77f3dba0e65cd3281f4b8fe59ebed7ab1fafd0c",
    }
    # The release targets 0016: reject incomplete chains, including missing new files.
    if not reviewed.keys() <= candidate.keys():
        return False
    if any(hashlib.sha256(candidate[name]).hexdigest() != sha for name, sha in reviewed.items()):
        return False
    added = candidate.keys() - existing.keys()
    return added <= reviewed.keys()


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
    # This check grants no deployment approval; all backup and readiness gates remain.
    if not compatible_migrations(migration_snapshot(root), migration_snapshot(old_root)):
        raise module.OperationError("Unreviewed or changed schema: deployment refused")
    original_args = module.parser().parse_args(["--root", str(old_root), "deploy"])
    original = module.Operations(original_args)
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise module.OperationError("Invalid release version")
    image = "safecheck:" + version + "-" + sha[:12]
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
        candidate = module.Operations(args)
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

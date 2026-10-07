#!/usr/bin/env python3
"""Upload and independently read back a private backup using an operator's rclone remote."""

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


class UploadError(RuntimeError):
    pass


def private_file(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise UploadError("Offsite files must be owned private regular files")


def upload(archive, checksum, config_path, *, runner=subprocess.run):
    for path in (archive, checksum, config_path):
        private_file(path)
    if not re.fullmatch(
        r"safecheck-\d{8}T\d{6}Z-[0-9a-f]{8}\.dump", archive.name
    ) or checksum.absolute() != Path(str(archive.absolute()) + ".sha256"):
        raise UploadError("Unexpected archive or checksum filename")
    with archive.open("rb") as file:
        expected = hashlib.file_digest(file, "sha256").hexdigest()
    if checksum.read_text().strip() != expected:
        raise UploadError("Local archive checksum mismatch")
    config = json.loads(config_path.read_text())
    if config.keys() != {"rclone_config", "remote_prefix", "rclone_binary"}:
        raise UploadError("Offsite configuration fields invalid")
    credential_file = Path(config["rclone_config"])
    binary = Path(config["rclone_binary"])
    remote = config["remote_prefix"]
    if not credential_file.is_absolute() or not binary.is_absolute():
        raise UploadError("Rclone paths must be absolute")
    private_file(credential_file)
    if (
        not isinstance(remote, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+:[^\r\n\x00]*", remote)
        or "REPLACE_WITH" in remote
        or "://" in remote
    ):
        raise UploadError("Configure an explicit rclone remote and private prefix")
    remote = remote.rstrip("/")
    prefix = [
        str(binary),
        "--config",
        str(credential_file),
        "--log-level",
        "ERROR",
        "--retries",
        "3",
        "--low-level-retries",
        "3",
    ]

    def call(*args):
        try:
            result = runner(
                prefix + list(args),
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=1800,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise UploadError("Offsite command unavailable or timed out") from error
        if result.returncode:
            raise UploadError("Offsite transfer or read-back verification failed")
        return result.stdout

    remote_archive = remote + "/" + archive.name
    remote_checksum = remote + "/" + checksum.name
    call("copyto", str(archive), remote_archive)
    call("copyto", str(checksum), remote_checksum)
    # Compare actual downloaded bytes even when the backend supplies no usable hash.
    # Limit the check to this archive pair rather than every retained backup.
    with tempfile.TemporaryDirectory(prefix="safecheck-offsite-") as directory:
        files = Path(directory) / "files"
        with open(files, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as file:
            file.write(archive.name + "\n" + checksum.name + "\n")
        call(
            "check",
            str(archive.parent),
            remote,
            "--download",
            "--one-way",
            "--files-from-raw",
            str(files),
        )
    downloaded_checksum = call("cat", remote_checksum)
    if downloaded_checksum.strip() != expected.encode("ascii"):
        raise UploadError("Downloaded offsite checksum mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("checksum", type=Path)
    args = parser.parse_args()
    config_path = Path(os.environ.get("SAFECHECK_OFFSITE_CONFIG", "/etc/safecheck/offsite.json"))
    try:
        upload(args.archive, args.checksum, config_path)
    except UploadError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Offsite configuration or backup is invalid.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

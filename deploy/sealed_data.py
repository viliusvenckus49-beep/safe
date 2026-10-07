#!/usr/bin/env python3
"""Authenticated encrypted snapshot transport. Private keys and plaintext are never logged."""

import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
CONTEXT = b"SAFECheck-legacy-v1\0"


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    path.chmod(0o600)


def snapshot_digest(data):
    obj = json.loads(data)
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def run(command):
    result = subprocess.run(command, capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("Encryption operation failed")
    return result.stdout


def seal(source, public_key, output):
    plaintext = source.read_bytes()
    if len(plaintext) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Snapshot too large")
    digest = snapshot_digest(plaintext)
    key = os.urandom(64)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        private_write(tmp / "material", key)
        wrapped = run(
            [
                "openssl",
                "pkeyutl",
                "-encrypt",
                "-pubin",
                "-inkey",
                str(public_key),
                "-in",
                str(tmp / "material"),
                "-pkeyopt",
                "rsa_padding_mode:oaep",
                "-pkeyopt",
                "rsa_oaep_md:sha256",
            ]
        )
        private_write(tmp / "password", key[:32].hex().encode() + b"\n")
        private_write(tmp / "source.gz", gzip.compress(plaintext))
        run(
            [
                "openssl",
                "enc",
                "-aes-256-cbc",
                "-salt",
                "-pbkdf2",
                "-iter",
                "200000",
                "-in",
                str(tmp / "source.gz"),
                "-out",
                str(tmp / "body"),
                "-pass",
                "file:" + str(tmp / "password"),
            ]
        )
        cipher = (tmp / "body").read_bytes()
    tag = hmac.new(
        key[32:], CONTEXT + digest.encode() + wrapped + cipher, hashlib.sha256
    ).hexdigest()
    envelope = {
        "format": 1,
        "snapshot_digest": digest,
        "encrypted_key": base64.b64encode(wrapped).decode(),
        "ciphertext": base64.b64encode(cipher).decode(),
        "mac": tag,
    }
    private_write(output, json.dumps(envelope, separators=(",", ":")).encode())
    return digest


def unseal(source, private_key, output, expected_digest):
    obj = json.loads(source.read_text())
    if obj["format"] != 1 or obj["snapshot_digest"] != expected_digest:
        raise ValueError("Unexpected snapshot")
    wrapped = base64.b64decode(obj["encrypted_key"], validate=True)
    cipher = base64.b64decode(obj["ciphertext"], validate=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        private_write(tmp / "wrapped", wrapped)
        key = run(
            [
                "openssl",
                "pkeyutl",
                "-decrypt",
                "-inkey",
                str(private_key),
                "-in",
                str(tmp / "wrapped"),
                "-pkeyopt",
                "rsa_padding_mode:oaep",
                "-pkeyopt",
                "rsa_oaep_md:sha256",
            ]
        )
        if len(key) != 64:
            raise ValueError("Invalid transport key")
        tag = hmac.new(
            key[32:], CONTEXT + expected_digest.encode() + wrapped + cipher, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(tag, obj["mac"]):
            raise ValueError("Snapshot authentication failed")
        private_write(tmp / "password", key[:32].hex().encode() + b"\n")
        private_write(tmp / "body", cipher)
        compressed = run(
            [
                "openssl",
                "enc",
                "-d",
                "-aes-256-cbc",
                "-pbkdf2",
                "-iter",
                "200000",
                "-in",
                str(tmp / "body"),
                "-pass",
                "file:" + str(tmp / "password"),
            ]
        )
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as gz:
        plaintext = gz.read(MAX_SNAPSHOT_BYTES + 1)
    if len(plaintext) > MAX_SNAPSHOT_BYTES or snapshot_digest(plaintext) != expected_digest:
        raise ValueError("Snapshot integrity failed")
    private_write(output, plaintext)


if __name__ == "__main__":
    try:
        mode, source, key, output, *rest = sys.argv[1:]
        if mode == "seal" and not rest:
            print(seal(Path(source), Path(key), Path(output)))
        elif mode == "unseal" and len(rest) == 1:
            unseal(Path(source), Path(key), Path(output), rest[0])
            print("Encrypted snapshot authentication and integrity passed.")
        else:
            raise ValueError("Invalid arguments")
    except Exception:
        raise SystemExit("Encrypted data transfer refused; no keys or records displayed") from None

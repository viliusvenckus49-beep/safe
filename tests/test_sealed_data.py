import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "sealed_data", Path(__file__).parents[1] / "deploy/sealed_data.py"
)
sealed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sealed)


@pytest.fixture
def encrypted_snapshot(tmp_path):
    private_key, public_key = tmp_path / "private", tmp_path / "public"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(private_key),
        ],
        check=True,
        capture_output=True,
    )
    public_key.write_bytes(
        subprocess.check_output(
            ["openssl", "pkey", "-in", str(private_key), "-pubout"], stderr=subprocess.DEVNULL
        )
    )
    original = tmp_path / "original"
    original.write_text(json.dumps({"data": ["Žodis", "История", 123]}))
    envelope = tmp_path / "envelope"
    digest = sealed.seal(original, public_key, envelope)
    return original, private_key, envelope, digest


def test_authenticated_roundtrip_and_private_output(encrypted_snapshot, tmp_path):
    original, key, envelope, digest = encrypted_snapshot
    output = tmp_path / "out"
    sealed.unseal(envelope, key, output, digest)
    assert original.read_bytes() == output.read_bytes()
    assert output.stat().st_mode & 0o777 == 0o600
    assert b"PRIVATE KEY" not in envelope.read_bytes()
    assert "История" not in envelope.read_text()


@pytest.mark.parametrize("field", ["mac", "ciphertext", "encrypted_key", "snapshot_digest"])
def test_tamper_is_rejected_without_plaintext(encrypted_snapshot, tmp_path, field):
    _, key, envelope, digest = encrypted_snapshot
    payload = json.loads(envelope.read_text())
    value = payload[field]
    payload[field] = ("0" if value[0] != "0" else "1") + value[1:]
    envelope.write_text(json.dumps(payload))
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        sealed.unseal(envelope, key, output, digest)
    assert not output.exists()


def test_cipher_for_another_snapshot_is_rejected(encrypted_snapshot, tmp_path):
    _, key, envelope, _ = encrypted_snapshot
    with pytest.raises(ValueError):
        sealed.unseal(envelope, key, tmp_path / "out", "f" * 64)
    assert not (tmp_path / "out").exists()

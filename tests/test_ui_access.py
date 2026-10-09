"""Test-only editor access stays private, persistent and separate from design exports."""

import json

import pytest

from app.test_ui.access import Access, user_id


def test_admin_grant_and_editor_promotion_survive_restart(tmp_path):
    path = tmp_path / "access.json"
    access = Access(path, frozenset({900}))
    assert access.add_editor(8425927753)
    assert access.add_admin(8425927753)
    restored = Access(path, frozenset({900}))
    assert restored.admins == frozenset({900, 8425927753})
    assert restored.editors == frozenset()
    assert not restored.add_editor(900)
    assert not restored.add_admin(8425927753)
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_permission_write_keeps_existing_roles_and_file(tmp_path, monkeypatch):
    path = tmp_path / "access.json"
    access = Access(path, frozenset({900}))
    access.add_editor(888)
    previous = path.read_bytes()

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr("app.test_ui.access.os.replace", fail)
    with pytest.raises(OSError):
        access.add_editor(777)
    assert path.read_bytes() == previous
    assert not access.can_edit(777)
    assert not list(tmp_path.glob(".ui-access-*"))


@pytest.mark.parametrize(
    "data",
    [
        {"version": 2, "admins": [], "editors": []},
        {"version": 1, "admins": [True], "editors": []},
        {"version": 1, "admins": [], "editors": [0]},
        {"version": 1, "admins": [], "editors": [888, 888]},
        {"version": 1, "admins": [888], "editors": [888]},
        {"version": 1, "admins": [], "editors": "888"},
        {"version": 1, "admins": [], "editors": [], "other": 888},
    ],
)
def test_corrupt_access_file_fails_closed_without_overwrite(tmp_path, data):
    path = tmp_path / "access.json"
    original = json.dumps(data)
    path.write_text(original)
    with pytest.raises(ValueError):
        Access(path, frozenset({900}))
    assert path.read_text() == original


def test_ids_are_numeric_and_support_telegram_large_ids():
    assert user_id(" 8425927753 ") == 8425927753
    assert user_id("9223372036854775807") == 9223372036854775807

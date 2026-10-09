"""Persistent private UI permissions, independent of production administrators."""

import json
import os
import tempfile
from pathlib import Path


def user_id(value: str) -> int:
    value = value.strip()
    if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 9223372036854775807:
        raise ValueError("A positive numeric Telegram user ID is required")
    return int(value)


class Access:
    def __init__(self, path: Path, administrators: frozenset[int]):
        self.path = path
        self.configured = administrators
        self.data: dict = {"version": 1, "admins": [], "editors": []}
        if path.exists():
            if path.stat().st_size > 100_000:
                raise ValueError("UI access file too large")
            loaded = json.loads(path.read_text())
            if (
                not isinstance(loaded, dict)
                or set(loaded) != set(self.data)
                or loaded["version"] != 1
            ):
                raise ValueError("Invalid UI access file")
            for role in ("admins", "editors"):
                ids = loaded[role]
                if (
                    not isinstance(ids, list)
                    or any(
                        type(item) is not int or not 0 < item <= 9223372036854775807 for item in ids
                    )
                    or len(ids) != len(set(ids))
                ):
                    raise ValueError("Invalid UI access IDs")
            if set(loaded["admins"]) & set(loaded["editors"]):
                raise ValueError("Duplicate UI access roles")
            self.data = loaded

    @property
    def admins(self) -> frozenset[int]:
        return self.configured | frozenset(self.data["admins"])

    @property
    def editors(self) -> frozenset[int]:
        return frozenset(self.data["editors"]) - self.admins

    def is_admin(self, actor: int) -> bool:
        return actor in self.admins

    def can_edit(self, actor: int) -> bool:
        return actor in self.admins or actor in self.editors

    def save(self, data: dict | None = None) -> None:
        candidate = self.data if data is None else data
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(dir=self.path.parent, prefix=".ui-access-")
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(candidate, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(filename, self.path)
            self.data = candidate
        finally:
            if os.path.exists(filename):
                os.unlink(filename)

    def add_editor(self, actor: int) -> bool:
        user_id(str(actor))
        if self.can_edit(actor):
            return False
        self.save({**self.data, "editors": sorted([*self.data["editors"], actor])})
        return True

    def add_admin(self, actor: int) -> bool:
        user_id(str(actor))
        if self.is_admin(actor):
            return False
        self.save(
            {
                **self.data,
                "admins": sorted([*self.data["admins"], actor]),
                "editors": [item for item in self.data["editors"] if item != actor],
            }
        )
        return True

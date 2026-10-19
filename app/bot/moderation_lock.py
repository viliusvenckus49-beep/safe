"""Serialize live moderation for one Telegram ID without retaining idle locks."""

import asyncio
from datetime import datetime
from typing import Any
from weakref import WeakValueDictionary

_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def has_permanent_ban(member: Any) -> bool:
    """A temporary ban is not a completed permanent SCAM restriction."""
    if getattr(member, "status", None) != "kicked":
        return False
    until = getattr(member, "until_date", None)
    return until is None or (isinstance(until, datetime) and until.timestamp() <= 0)


def user_moderation_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock

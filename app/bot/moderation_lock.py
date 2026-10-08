"""Serialize live moderation for one Telegram ID without retaining idle locks."""

import asyncio
from weakref import WeakValueDictionary

_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()


def user_moderation_lock(user_id: int) -> asyncio.Lock:
    lock = _locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[user_id] = lock
    return lock

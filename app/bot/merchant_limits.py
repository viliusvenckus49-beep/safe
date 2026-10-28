"""Atomic Redis search throttling; bounded local fallback for memory-only tests."""

from time import monotonic

from app.errors import DomainError


class SearchLimiter:
    def __init__(self):
        self.recent: dict[int, float] = {}

    async def admit(self, state, actor):
        redis = getattr(state.storage, "redis", None)
        if redis is not None:
            allowed = await redis.set(
                f"safecheck:merchants:search:{state.key.bot_id}:{actor}", "1", nx=True, ex=2
            )
        else:
            current = monotonic()
            if len(self.recent) > 10000:
                self.recent = {k: v for k, v in self.recent.items() if current - v < 2}
            allowed = current - self.recent.get(actor, -100) > 2
            if allowed:
                self.recent[actor] = current
        if not allowed:
            raise DomainError("merchant_rate")

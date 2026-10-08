"""Soft /ask bursts: gradual refill, short duplicate suppression and bounded memory."""

from collections import OrderedDict
from dataclasses import dataclass, field

ASK_BURST = 10
ASK_REFILL_SECONDS = 6.0
ASK_DUPLICATE_SECONDS = 10.0
ASK_IDLE_SECONDS = 120.0
ASK_MAX_USERS = 10_000


@dataclass
class _Budget:
    updated: float
    tokens: float = ASK_BURST
    targets: dict[str, float] = field(default_factory=dict)


class AskLimiter:
    def __init__(self) -> None:
        self._users: OrderedDict[int, _Budget] = OrderedDict()

    def admit(self, actor: int, target: str, current: float) -> str | None:
        """Return a rejection reason, without extending the user's waiting time."""
        while self._users:
            oldest = next(iter(self._users.values()))
            if current - oldest.updated < ASK_IDLE_SECONDS:
                break
            self._users.popitem(last=False)
        budget = self._users.get(actor)
        if budget is None:
            budget = _Budget(updated=current)
            self._users[actor] = budget
        self._users.move_to_end(actor)
        if len(self._users) > ASK_MAX_USERS:
            self._users.popitem(last=False)
        budget.tokens = min(
            ASK_BURST, budget.tokens + max(0, current - budget.updated) / ASK_REFILL_SECONDS
        )
        budget.updated = current
        budget.targets = {
            key: seen
            for key, seen in budget.targets.items()
            if current - seen < ASK_DUPLICATE_SECONDS
        }
        key = target.casefold()
        if key in budget.targets:
            return "duplicate"
        if budget.tokens < 1 - 1e-9:
            return "burst"
        budget.tokens = max(0, budget.tokens - 1)
        budget.targets[key] = current
        return None

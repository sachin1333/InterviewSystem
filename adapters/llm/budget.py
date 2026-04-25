"""Per-session cost budget tracker.

Usage:
    budget = SessionBudget(session_id="s-abc", cap_usd=0.40)
    debit = budget.debit(tokens_in=500, tokens_out=200,
                         price_in_per_1m=0.60, price_out_per_1m=2.50)
    if debit.exceeded:
        # emit BudgetExceeded event
        ...
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock


@dataclass
class DebitResult:
    cost_usd: float
    total_usd: float
    exceeded: bool
    over_by_usd: float


@dataclass
class SessionBudget:
    session_id: str
    cap_usd: float = 0.40
    _total: float = field(default=0.0, init=False, repr=False)
    _lock: Lock = field(default_factory=Lock, init=False, repr=False)

    def debit(
        self,
        *,
        tokens_in: int,
        tokens_out: int,
        price_in_per_1m: float,
        price_out_per_1m: float,
    ) -> DebitResult:
        cost = (tokens_in * price_in_per_1m + tokens_out * price_out_per_1m) / 1_000_000
        with self._lock:
            self._total += cost
            total = self._total
        exceeded = total > self.cap_usd
        return DebitResult(
            cost_usd=cost,
            total_usd=total,
            exceeded=exceeded,
            over_by_usd=max(0.0, total - self.cap_usd),
        )

    @property
    def total_usd(self) -> float:
        return self._total

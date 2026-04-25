"""In-process latency estimator.

Tracks P50/P95 per (tier, task) via a sliding window of 1000 calls.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Lock
from typing import Literal

type Tier = Literal["cheap", "mid", "top"]

_WINDOW = 1000


@dataclass
class LatencyRecord:
    tier: str
    task: str
    wall_ms: int
    ttft_ms: int | None
    tokens_in: int
    tokens_out: int
    outcome: str  # "ok" | "timeout" | "error" | "fallback"


@dataclass
class LatencyStats:
    p50_ms: float
    p95_ms: float
    count: int


class LatencyTracker:
    """Thread-safe sliding-window latency tracker."""

    def __init__(self, window: int = _WINDOW) -> None:
        self._window = window
        self._records: deque[LatencyRecord] = deque(maxlen=window)
        self._lock = Lock()

    def record(self, rec: LatencyRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def stats(self, *, tier: str | None = None, task: str | None = None) -> LatencyStats:
        with self._lock:
            records = list(self._records)
        if tier:
            records = [r for r in records if r.tier == tier]
        if task:
            records = [r for r in records if r.task == task]
        if not records:
            return LatencyStats(p50_ms=0.0, p95_ms=0.0, count=0)
        wall_times = sorted(r.wall_ms for r in records)
        n = len(wall_times)
        return LatencyStats(
            p50_ms=wall_times[int(n * 0.50)],
            p95_ms=wall_times[int(n * 0.95)],
            count=n,
        )

    def emit_log_line(self, rec: LatencyRecord) -> str:
        """Format a structured log line for the record."""
        return (
            f"llm_latency tier={rec.tier} task={rec.task} wall_ms={rec.wall_ms}"
            f" ttft_ms={rec.ttft_ms} tokens_in={rec.tokens_in}"
            f" tokens_out={rec.tokens_out} outcome={rec.outcome}"
        )


# Module-level default tracker (used by ModelRouter if no tracker injected).
_default_tracker = LatencyTracker()


def default_tracker() -> LatencyTracker:
    return _default_tracker

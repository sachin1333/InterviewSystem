"""Latency probe: run N synthetic challenger drafts, report P50/P95/P99.

Usage:
    python tools/latency_probe.py [--n 200] [--assert-p95 2000]
"""
from __future__ import annotations

import argparse
import sys
import time

# Allow running from project root.
sys.path.insert(0, ".")

from adapters.llm.fake_router import FakeRouter
from adapters.llm.latency import LatencyRecord, LatencyTracker
from adapters.llm.router import ModelRouter


def run_probe(n: int, assert_p95_ms: int | None) -> None:
    provider = FakeRouter(latency=0.0)
    router = ModelRouter(provider)
    tracker = LatencyTracker()

    for i in range(n):
        start = time.perf_counter()
        try:
            router.call_json(
                tier="top",
                prompt=f"Draft question #{i}",
                schema={"prompt_markdown", "turn_kind"},
                deadline_ms=2000,
            )
            outcome = "ok"
        except Exception:
            outcome = "error"
        wall_ms = int((time.perf_counter() - start) * 1000)
        tracker.record(LatencyRecord(
            tier="top", task="challenger.draft",
            wall_ms=wall_ms, ttft_ms=None,
            tokens_in=50, tokens_out=100, outcome=outcome,
        ))

    stats = tracker.stats(tier="top", task="challenger.draft")
    print(f"N={stats.count}  P50={stats.p50_ms}ms  P95={stats.p95_ms}ms")

    if assert_p95_ms is not None and stats.p95_ms > assert_p95_ms:
        print(f"FAIL: P95 {stats.p95_ms}ms > {assert_p95_ms}ms threshold")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--assert-p95", type=int, default=None)
    args = parser.parse_args()
    run_probe(args.n, args.assert_p95)

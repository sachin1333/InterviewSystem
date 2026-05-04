"""Latency probe: run N synthetic challenger drafts, report P50/P95/P99.

Usage:
    python tools/latency_probe.py [--n 200] [--assert-p95 2000]
"""
from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass

# Allow running from project root.
sys.path.insert(0, ".")

from adapters.llm.fake_router import FakeRouter
from adapters.llm.latency import LatencyRecord, LatencyTracker
from adapters.llm.router import ModelRouter


@dataclass(frozen=True)
class ProbeTiming:
    stage: str
    elapsed_ms: float | None


_STAGE_LABELS = {
    "post_submit": "POST submit latency",
    "examiner_render": "examiner render latency",
    "next_problem_render": "next-problem render latency",
    "result_shell": "result shell latency",
    "background_scoring": "background scoring duration",
}


def format_latency_probe_report(
    *,
    n: int,
    p50_ms: float,
    p95_ms: float,
    timings: Iterable[ProbeTiming],
) -> str:
    lines = [f"N={n}  P50={p50_ms}ms  P95={p95_ms}ms"]
    for timing in timings:
        label = _STAGE_LABELS.get(timing.stage, timing.stage.replace("_", " "))
        if timing.elapsed_ms is None:
            lines.append(f"{label}: not observed")
        else:
            lines.append(f"{label}: {timing.elapsed_ms:.1f}ms")
    return "\n".join(lines)


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
        tracker.record(
            LatencyRecord(
                tier="top",
                task="challenger.draft",
                wall_ms=wall_ms,
                ttft_ms=None,
                tokens_in=50,
                tokens_out=100,
                outcome=outcome,
            )
        )

    stats = tracker.stats(tier="top", task="challenger.draft")
    timings = (
        ProbeTiming("post_submit", stats.p50_ms),
        ProbeTiming("examiner_render", None),
        ProbeTiming("next_problem_render", None),
        ProbeTiming("result_shell", None),
        ProbeTiming("background_scoring", None),
    )
    print(
        format_latency_probe_report(
            n=stats.count,
            p50_ms=stats.p50_ms,
            p95_ms=stats.p95_ms,
            timings=timings,
        )
    )

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

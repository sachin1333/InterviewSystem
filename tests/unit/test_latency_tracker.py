from adapters.llm.latency import LatencyRecord, LatencyTracker


def _rec(wall_ms: int) -> LatencyRecord:
    return LatencyRecord(
        tier="top", task="challenger.draft",
        wall_ms=wall_ms, ttft_ms=None,
        tokens_in=50, tokens_out=100, outcome="ok",
    )


def test_empty_stats() -> None:
    t = LatencyTracker()
    s = t.stats()
    assert s.count == 0
    assert s.p50_ms == 0.0


def test_p95_computed() -> None:
    t = LatencyTracker()
    for ms in range(1, 101):   # 1..100
        t.record(_rec(ms))
    s = t.stats(tier="top")
    assert s.count == 100
    assert s.p95_ms >= 95


def test_emit_log_line_format() -> None:
    t = LatencyTracker()
    rec = _rec(42)
    line = t.emit_log_line(rec)
    assert "wall_ms=42" in line
    assert "tier=top" in line

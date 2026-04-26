from __future__ import annotations

from core.events import LatencyObserved
from tools.voice_latency_report import parse_since, render_markdown, summarize_latencies


def test_summarize_latencies_emits_percentiles() -> None:
    summary = summarize_latencies(
        [
            LatencyObserved(
                turn_id=f"t{i}",
                stt_first_partial_ms=100 + i,
                stt_final_ms=200 + i,
                llm_ttft_ms=300 + i,
                tts_first_byte_ms=90 + i,
                end_to_end_ms=700 + i,
            )
            for i in range(5)
        ]
    )
    assert summary["end_to_end_ms"].p50 >= 700
    assert summary["tts_first_byte_ms"].max >= 90
    assert "| p95 |" in render_markdown(summary)


def test_parse_since_hours() -> None:
    result = parse_since("1h")
    assert result is not None
